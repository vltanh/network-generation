// Place 2-D points so that no triple is visibly colinear.
//
// Same model as optim_positions.mjs (kept for reference / quick edits).
// C++ port runs ~50-100x faster, lets the SA cover many more restarts
// + iterations than the JS version.
//
// Build:  g++ -O3 -std=c++17 optim_positions.cpp -o optim_positions
// Run:    ./optim_positions <input.json> > output.json
//
// Input JSON shape:
// {
//   "positions": { "1": {"x": -126, "y": -180}, ... },
//   "target": 22,           px; threshold for "colinear"
//   "minPair": 58,          px; minimum pairwise distance allowed
//   "maxDisplace": 70,      px; cap on |new - original|
//   "restarts": 200,
//   "iters": 500000,
//   "nudgePx": 8,
//   "seedPerturb": 40,
//   "initialTemp": 200,
//   "coolRate": 0.99998,
//   "tBetween": [0.05, 0.95]
// }
//
// Output: best positions found + summary scores. Non-zero exit code
// if no feasible config was found.
//
// Dependencies: header-only nlohmann/json (vendored as a single header
// next to this file as `json.hpp` — fetch from
// https://github.com/nlohmann/json/releases).

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <map>
#include <random>
#include <string>
#include <vector>

#include "json.hpp"
using nlohmann::json;

struct Opts {
    std::vector<std::string> ids;
    std::vector<double> origX, origY;
    double target = 22.0;
    double minPair = 58.0;
    double maxDisplace = 70.0;
    int restarts = 200;
    int iters = 500000;
    double nudgePx = 8.0;
    double seedPerturb = 40.0;
    double initialTemp = 200.0;
    double coolRate = 0.99998;
    double tLo = 0.05, tHi = 0.95;
    // Per-cluster cohesion: list of node-id groups; for each group the
    // maximum pairwise distance is capped at clusterMaxDiameter so the
    // members read as a coherent cluster.
    std::vector<std::vector<int>> clusters;  // indices into ids
    double clusterMaxDiameter = 0.0;         // 0 = disabled
    double minCross = 0.0;                   // hard floor on triple distance; 0 = disabled
    // Per-node cluster id (-1 = not in any cluster). Triples whose
    // three nodes share a cluster id are weighted by intraWeight in
    // the penalty sum, so intra-cluster colinearities cost more.
    std::vector<int> clusterOf;              // size == ids.size()
    double intraWeight = 3.0;
};

struct Score {
    double worst = std::numeric_limits<double>::infinity();
    int count = 0;
    double penalty = 0.0;
    double minPair = std::numeric_limits<double>::infinity();
};

static Score scorePos(const std::vector<double>& X, const std::vector<double>& Y,
                      double target, double tLo, double tHi,
                      const std::vector<int>& clusterOf, double intraWeight) {
    Score s;
    const int N = (int)X.size();
    for (int i = 0; i < N; i++) for (int j = i + 1; j < N; j++) {
        double dx = X[j] - X[i], dy = Y[j] - Y[i];
        double d = std::hypot(dx, dy);
        if (d < s.minPair) s.minPair = d;
    }
    bool useCluster = !clusterOf.empty();
    for (int i = 0; i < N; i++) for (int j = i + 1; j < N; j++) {
        double ax = X[i], ay = Y[i], bx = X[j], by = Y[j];
        double dx = bx - ax, dy = by - ay;
        double L2 = dx * dx + dy * dy;
        if (L2 < 1.0) continue;
        double L = std::sqrt(L2);
        int ci = useCluster ? clusterOf[i] : -2;
        int cj = useCluster ? clusterOf[j] : -2;
        for (int k = 0; k < N; k++) {
            if (k == i || k == j) continue;
            double cx = X[k], cy = Y[k];
            double cross = std::abs(dx * (ay - cy) - dy * (ax - cx)) / L;
            double t = ((cx - ax) * dx + (cy - ay) * dy) / L2;
            if (t > tLo && t < tHi) {
                if (cross < s.worst) s.worst = cross;
                if (cross < target) s.count++;
                // Penalty: max(target - cross, 0)^2 sums over every
                // triple, so the optimizer chases the average +
                // worst together. Intra-cluster triples (all three
                // share the same cluster id) get a higher weight so
                // members of one cluster don't read as colinear among
                // themselves.
                double w = 1.0;
                if (useCluster && intraWeight > 1.0) {
                    int ck = clusterOf[k];
                    if (ci >= 0 && ci == cj && ci == ck) w = intraWeight;
                }
                // Combined cost. Quadratic deficit drives the average
                // colinearity down; a 1/cross^4 divergence below half
                // of target makes near-zero perpendicular distances
                // catastrophic so the SA refuses to leave a high-worst
                // attractor.
                double dft = target - cross;
                if (dft > 0) s.penalty += w * dft * dft;
                if (cross < target * 0.5) {
                    double inv = 1.0 / (cross + 0.3);
                    double iv2 = inv * inv;
                    s.penalty += w * iv2 * iv2 * 1000.0;
                }
            }
        }
    }
    return s;
}

static bool withinBudget(const std::vector<double>& X, const std::vector<double>& Y,
                         const std::vector<double>& OX, const std::vector<double>& OY,
                         double maxDisp) {
    const int N = (int)X.size();
    for (int i = 0; i < N; i++) {
        double dx = X[i] - OX[i], dy = Y[i] - OY[i];
        if (std::hypot(dx, dy) > maxDisp) return false;
    }
    return true;
}

static Opts parseInput(const std::string& path) {
    std::ifstream f(path);
    if (!f) { std::cerr << "cannot open " << path << "\n"; std::exit(2); }
    json j;
    f >> j;
    Opts o;
    if (!j.contains("positions")) { std::cerr << "input must have .positions\n"; std::exit(2); }
    for (auto it = j["positions"].begin(); it != j["positions"].end(); ++it) {
        o.ids.push_back(it.key());
        o.origX.push_back(it.value()["x"].get<double>());
        o.origY.push_back(it.value()["y"].get<double>());
    }
    if (j.contains("target"))      o.target = j["target"].get<double>();
    if (j.contains("minPair"))     o.minPair = j["minPair"].get<double>();
    if (j.contains("maxDisplace")) o.maxDisplace = j["maxDisplace"].get<double>();
    if (j.contains("restarts"))    o.restarts = j["restarts"].get<int>();
    if (j.contains("iters"))       o.iters = j["iters"].get<int>();
    if (j.contains("nudgePx"))     o.nudgePx = j["nudgePx"].get<double>();
    if (j.contains("seedPerturb")) o.seedPerturb = j["seedPerturb"].get<double>();
    if (j.contains("initialTemp")) o.initialTemp = j["initialTemp"].get<double>();
    if (j.contains("coolRate"))    o.coolRate = j["coolRate"].get<double>();
    if (j.contains("tBetween")) {
        o.tLo = j["tBetween"][0].get<double>();
        o.tHi = j["tBetween"][1].get<double>();
    }
    if (j.contains("clusters")) {
        std::map<std::string, int> idIndex;
        for (int i = 0; i < (int)o.ids.size(); i++) idIndex[o.ids[i]] = i;
        o.clusterOf.assign(o.ids.size(), -1);
        int gid = 0;
        for (const auto& g : j["clusters"]) {
            std::vector<int> idxs;
            for (const auto& nid : g) {
                std::string s = nid.get<std::string>();
                auto it = idIndex.find(s);
                if (it != idIndex.end()) {
                    idxs.push_back(it->second);
                    o.clusterOf[it->second] = gid;
                }
            }
            if (!idxs.empty()) { o.clusters.push_back(idxs); gid++; }
        }
    }
    if (j.contains("clusterMaxDiameter")) o.clusterMaxDiameter = j["clusterMaxDiameter"].get<double>();
    if (j.contains("intraWeight")) o.intraWeight = j["intraWeight"].get<double>();
    if (j.contains("minCross")) o.minCross = j["minCross"].get<double>();
    return o;
}

static bool clusterDiameterOK(const std::vector<double>& X, const std::vector<double>& Y,
                              const Opts& o) {
    if (o.clusterMaxDiameter <= 0 || o.clusters.empty()) return true;
    const double dmax = o.clusterMaxDiameter;
    for (const auto& g : o.clusters) {
        for (size_t i = 0; i < g.size(); i++) for (size_t j = i + 1; j < g.size(); j++) {
            double dx = X[g[i]] - X[g[j]], dy = Y[g[i]] - Y[g[j]];
            if (std::hypot(dx, dy) > dmax) return false;
        }
    }
    return true;
}

int main(int argc, char** argv) {
    if (argc < 2) { std::cerr << "usage: " << argv[0] << " <input.json>\n"; return 2; }
    Opts opts = parseInput(argv[1]);
    const int N = (int)opts.ids.size();

    std::random_device rd;
    std::mt19937_64 rng(rd());
    std::uniform_real_distribution<double> uni(-1.0, 1.0);
    std::uniform_real_distribution<double> u01(0.0, 1.0);
    std::uniform_int_distribution<int> nodeIdx(0, N - 1);
    std::uniform_int_distribution<int> nudgeCount(0, 2); // returns 0..2; we add 1

    std::vector<double> bestX, bestY;
    Score bestScore; bestScore.worst = 0; bestScore.penalty = std::numeric_limits<double>::infinity();
    long long totalAccept = 0;

    for (int restart = 0; restart < opts.restarts; restart++) {
        std::vector<double> X(opts.origX), Y(opts.origY);
        if (restart > 0) {
            for (int i = 0; i < N; i++) {
                X[i] = std::round(opts.origX[i] + uni(rng) * opts.seedPerturb);
                Y[i] = std::round(opts.origY[i] + uni(rng) * opts.seedPerturb);
            }
        }
        // Push to feasibility on the seed if it isn't.
        Score curScore = scorePos(X, Y, opts.target, opts.tLo, opts.tHi, opts.clusterOf, opts.intraWeight);
        for (int attempt = 0; attempt < 300 && curScore.minPair < opts.minPair; attempt++) {
            int id = nodeIdx(rng);
            double tx = std::round(X[id] + uni(rng) * 18);
            double ty = std::round(Y[id] + uni(rng) * 18);
            std::swap(X[id], tx);
            std::swap(Y[id], ty);
            if (!withinBudget(X, Y, opts.origX, opts.origY, opts.maxDisplace)) {
                std::swap(X[id], tx); std::swap(Y[id], ty);
                continue;
            }
            Score s = scorePos(X, Y, opts.target, opts.tLo, opts.tHi, opts.clusterOf, opts.intraWeight);
            if (s.minPair > curScore.minPair) curScore = s;
            else { std::swap(X[id], tx); std::swap(Y[id], ty); }
        }
        if (curScore.minPair < opts.minPair) continue;

        double temp = opts.initialTemp;
        for (int iter = 0; iter < opts.iters; iter++) {
            int n = nudgeCount(rng) + 1;
            std::vector<int> hits(n);
            std::vector<double> oldX(n), oldY(n);
            for (int i = 0; i < n; i++) {
                hits[i] = nodeIdx(rng);
                oldX[i] = X[hits[i]];
                oldY[i] = Y[hits[i]];
                X[hits[i]] = std::round(X[hits[i]] + uni(rng) * opts.nudgePx);
                Y[hits[i]] = std::round(Y[hits[i]] + uni(rng) * opts.nudgePx);
            }
            bool ok = withinBudget(X, Y, opts.origX, opts.origY, opts.maxDisplace)
                      && clusterDiameterOK(X, Y, opts);
            Score s;
            if (ok) {
                s = scorePos(X, Y, opts.target, opts.tLo, opts.tHi, opts.clusterOf, opts.intraWeight);
                if (s.minPair < opts.minPair) ok = false;
                if (opts.minCross > 0 && s.worst < opts.minCross) ok = false;
            }
            bool accept = false;
            if (ok) {
                // Energy = the squared-deficit penalty summed over
                // every triple (intra-cluster triples weighted heavier
                // — see scorePos). Reducing it pushes both the worst
                // case and the count of near-colinear triples down.
                double dE = s.penalty - curScore.penalty;
                if (dE < 0 || u01(rng) < std::exp(-dE / temp)) accept = true;
            }
            if (accept) {
                curScore = s;
                totalAccept++;
                if (s.worst > bestScore.worst ||
                    (s.worst == bestScore.worst && s.penalty < bestScore.penalty)) {
                    bestScore = s; bestX = X; bestY = Y;
                }
            } else {
                for (int i = 0; i < n; i++) {
                    X[hits[i]] = oldX[i];
                    Y[hits[i]] = oldY[i];
                }
            }
            temp *= opts.coolRate;
        }
        std::cerr << "restart " << restart << " done: cur worst=" << curScore.worst
                  << " best worst=" << bestScore.worst << " best minPair=" << bestScore.minPair
                  << " accepts=" << totalAccept << "\n";
    }
    if (bestX.empty()) { std::cerr << "no feasible config found\n"; return 1; }

    json out;
    out["positions"] = json::object();
    for (int i = 0; i < N; i++) {
        out["positions"][opts.ids[i]] = {
            {"x", (int)bestX[i]},
            {"y", (int)bestY[i]},
        };
    }
    out["score"] = {
        {"worst", std::round(bestScore.worst * 100) / 100.0},
        {"countUnderTarget", bestScore.count},
        {"minPair", std::round(bestScore.minPair * 100) / 100.0},
        {"penalty", bestScore.penalty},
    };
    std::cout << out.dump(2) << "\n";
    return 0;
}
