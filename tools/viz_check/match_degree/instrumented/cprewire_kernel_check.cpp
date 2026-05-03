// Standalone reference kernel mirroring the cluster_preserving_rewire
// SPEC implemented by:
//   - matcher.html's runClusterPreservingRewire (vltanh.github.io/netgen/
//     js/match_degree_kernel.js, lines 795+); and
//   - canonical's match_missing_degrees_cluster_preserving_rewire +
//     graph_utils.cluster_preserving_2opt_rewire algorithm (without the
//     gt.generate_sbm dependency, which is non-portable).
//
// Phase 1 (per-bp stub draw + pair):
//   For each (B_i, B_j) bp with bpBudget > 0, draw `cnt` pairs via
//   weighted-random source + weighted-random partner restricted to the
//   bp's blocks, with weights = current residual on each node. Decrement
//   the weight after each pick (so the same node can be picked twice but
//   only with its remaining residual). Shuffle the pairs once
//   (Fisher-Yates). Self-loops + same-canonical-pair duplicates queue
//   for repair; everything else lands in valid_pool[bp].
//
// Phase 2 (2-opt repair, mirrors graph_utils.cluster_preserving_2opt_rewire):
//   Up to 10 retry passes. Per invalid pair (u, v): pick a swap partner
//   (x, y) from valid_pool[bp_of(u, v)] uniformly at random. Inter-bp
//   recombination is deterministic (route the AAA endpoint to the BBB
//   endpoint). Intra-bp gets a coin flip between (u-x, v-y) and
//   (u-y, v-x). Reject if the swap would introduce a self-loop, a
//   pre-existing pair, or a duplicate of the swap partner; otherwise
//   commit the swap.
//
// Phase 3 (commit + filter):
//   Walk valid_pool, drop pairs whose endpoints are already adjacent in
//   the input graph (exist_neighbor), accept the rest.
//
// PRNG: std::mt19937 seeded by the JSON's "seed" field. The JS replay
// at tools/viz_check/match_degree/kernel_check.mjs consumes this binary's
// trace and reproduces the edges byte-for-byte.
//
// Build:
//   bash tools/viz_check/match_degree/instrumented/build.sh
// Run:
//   echo FIXTURE_JSON | /tmp/md_cprewire_kernel_check
//
// Output JSON shape:
//   { "edges":  [[u, v], ...],
//     "trace":  [{kind, ...}, ...] }
//
// trace.kind is one of:
//   "stub":    { pair: "bi-bj", side: "u"|"v", node }
//   "shuffle": { pair: "bi-bj", j_seq: [j0, j1, ...] }
//   "repair":  { attempt, u, v, bp: "A-B", idx, coin: 0|1|null }
//
// Trace records the *result* of each RNG-influenced decision so the JS
// replay can short-circuit its own RNG and apply the same decisions.

#include <algorithm>
#include <array>
#include <cstdint>
#include <cstdio>
#include <iostream>
#include <map>
#include <random>
#include <set>
#include <sstream>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

// ----- minimal JSON parsing (input fixture) ------------------------------
struct JParse {
    const std::string& s;
    size_t p = 0;
    explicit JParse(const std::string& src) : s(src) {}
    void skip() { while (p < s.size() && std::isspace((unsigned char)s[p])) p++; }
    char peek() { skip(); return p < s.size() ? s[p] : '\0'; }
    void expect(char c) {
        skip();
        if (p >= s.size() || s[p] != c)
            throw std::runtime_error("expected " + std::string(1, c) + " at " + std::to_string(p));
        p++;
    }
    int64_t readInt() {
        skip();
        size_t start = p;
        if (s[p] == '-') p++;
        while (p < s.size() && std::isdigit((unsigned char)s[p])) p++;
        return std::stoll(s.substr(start, p - start));
    }
    std::string readKey() {
        skip(); expect('"');
        size_t start = p;
        while (p < s.size() && s[p] != '"') p++;
        std::string k = s.substr(start, p - start);
        expect('"'); skip(); expect(':');
        return k;
    }
    std::string readString() {
        skip(); expect('"');
        size_t start = p;
        while (p < s.size() && s[p] != '"') p++;
        std::string out = s.substr(start, p - start);
        expect('"');
        return out;
    }
    std::vector<int64_t> readIntArr() {
        std::vector<int64_t> v; expect('[');
        while (peek() != ']') { v.push_back(readInt()); if (peek() == ',') p++; }
        expect(']'); return v;
    }
    std::vector<std::pair<std::string, int64_t>> readStrIntDict() {
        std::vector<std::pair<std::string, int64_t>> out;
        expect('{');
        while (peek() != '}') {
            std::string k = readKey();
            int64_t v = readInt();
            out.emplace_back(k, v);
            if (peek() == ',') p++;
        }
        expect('}');
        return out;
    }
    std::vector<std::pair<std::string, std::vector<int64_t>>> readStrArrDict() {
        std::vector<std::pair<std::string, std::vector<int64_t>>> out;
        expect('{');
        while (peek() != '}') {
            std::string k = readKey();
            auto v = readIntArr();
            out.emplace_back(k, v);
            if (peek() == ',') p++;
        }
        expect('}');
        return out;
    }
};

// ----- fixture --------------------------------------------------------------
struct Fixture {
    int64_t seed = 1;
    std::vector<int64_t> iids;
    std::unordered_map<int64_t, int64_t> residual;
    std::unordered_map<int64_t, std::unordered_set<int64_t>> existNeighbor;
    std::unordered_map<int64_t, int64_t> b;
    std::map<std::pair<int64_t, int64_t>, int64_t> bpBudget;
};

static std::string mkBpKey(int64_t a, int64_t b) {
    std::ostringstream o;
    if (a < b) o << a << "-" << b;
    else o << b << "-" << a;
    return o.str();
}

static std::pair<int64_t, int64_t> bpOf(const Fixture& fx, int64_t u, int64_t v) {
    int64_t bu = fx.b.at(u), bv = fx.b.at(v);
    return {std::min(bu, bv), std::max(bu, bv)};
}

static std::pair<int64_t, int64_t> normEdge(int64_t u, int64_t v) {
    return {std::min(u, v), std::max(u, v)};
}

static std::string edgeKey(int64_t u, int64_t v) {
    auto e = normEdge(u, v);
    return std::to_string(e.first) + "-" + std::to_string(e.second);
}

// ----- trace recording ------------------------------------------------------
struct TraceEntry {
    std::string kind;
    std::string pair;
    std::string side;
    int64_t node = 0;
    std::vector<int64_t> j_seq;
    int64_t attempt = 0;
    int64_t u = 0, v = 0;
    std::string bp;
    int64_t idx = 0;
    int coin = -1;  // -1 = not applicable
};

static std::vector<TraceEntry> g_trace;

// ----- weighted random pick -------------------------------------------------
// Mirrors the JS drawStub: scan cumulative weights with rng() * total, pick
// the first node whose cumulative residual >= r. Returns -1 if total <= 0.
template <class RNG>
int64_t drawStub(const std::vector<int64_t>& blockNodes,
                 const std::unordered_map<int64_t, int64_t>& localRes,
                 RNG& rng) {
    int64_t total = 0;
    for (auto n : blockNodes) {
        auto it = localRes.find(n);
        if (it != localRes.end() && it->second > 0) total += it->second;
    }
    if (total <= 0) return -1;
    std::uniform_real_distribution<double> d(0.0, 1.0);
    double r = d(rng) * (double)total;
    for (auto n : blockNodes) {
        auto it = localRes.find(n);
        int64_t w = (it != localRes.end()) ? std::max<int64_t>(0, it->second) : 0;
        r -= (double)w;
        if (r <= 0.0) return n;
    }
    return blockNodes.back();
}

// ----- main -----------------------------------------------------------------
int main() {
    std::stringstream ss; ss << std::cin.rdbuf();
    std::string src = ss.str();
    JParse jp(src);

    Fixture fx;

    jp.expect('{');
    while (jp.peek() != '}') {
        std::string k = jp.readKey();
        if (k == "seed") fx.seed = jp.readInt();
        else if (k == "iids") fx.iids = jp.readIntArr();
        else if (k == "residual") {
            for (auto& kv : jp.readStrIntDict())
                fx.residual[std::stoll(kv.first)] = kv.second;
        }
        else if (k == "exist_neighbor") {
            for (auto& kv : jp.readStrArrDict()) {
                int64_t u = std::stoll(kv.first);
                fx.existNeighbor[u] = std::unordered_set<int64_t>(
                    kv.second.begin(), kv.second.end());
            }
        }
        else if (k == "b") {
            for (auto& kv : jp.readStrIntDict())
                fx.b[std::stoll(kv.first)] = kv.second;
        }
        else if (k == "bp_budget") {
            for (auto& kv : jp.readStrIntDict()) {
                auto dash = kv.first.find('-');
                int64_t a = std::stoll(kv.first.substr(0, dash));
                int64_t bb = std::stoll(kv.first.substr(dash + 1));
                fx.bpBudget[{a, bb}] = kv.second;
            }
        }
        else throw std::runtime_error("unexpected key " + k);
        if (jp.peek() == ',') jp.p++;
    }
    jp.expect('}');

    // Ensure every iid has an exist-neighbor entry (default empty).
    for (auto iid : fx.iids) {
        if (fx.existNeighbor.find(iid) == fx.existNeighbor.end())
            fx.existNeighbor[iid] = {};
    }

    std::mt19937 rng((uint32_t)fx.seed);

    // Group nodes by block (sorted by iid for stable iteration).
    std::map<int64_t, std::vector<int64_t>> nodesByBlock;
    for (auto& kv : fx.residual) {
        if (kv.second > 0) nodesByBlock[fx.b.at(kv.first)].push_back(kv.first);
    }
    for (auto& kv : nodesByBlock) std::sort(kv.second.begin(), kv.second.end());

    // Local residual snapshot (mutated by drawStub).
    std::unordered_map<int64_t, int64_t> localResidual = fx.residual;

    // valid_pool[bp_key] -> list of normalized edges; valid_set tracks
    // membership for fast contains().
    std::map<std::string, std::vector<std::pair<int64_t, int64_t>>> validPool;
    std::unordered_set<std::string> validSet;
    std::vector<std::pair<int64_t, int64_t>> invalidEdges;

    // Phase 1: walk bps in sorted-key order (matches JS sortedKeys).
    std::vector<std::pair<std::pair<int64_t, int64_t>, int64_t>> sortedBps;
    for (auto& kv : fx.bpBudget) {
        if (kv.second > 0) sortedBps.emplace_back(kv.first, kv.second);
    }
    std::sort(sortedBps.begin(), sortedBps.end(),
              [](const auto& a, const auto& b) {
                  return mkBpKey(a.first.first, a.first.second) <
                         mkBpKey(b.first.first, b.first.second);
              });

    for (auto& bpEntry : sortedBps) {
        auto bp = bpEntry.first;
        int64_t cnt = bpEntry.second;
        std::string bpStr = mkBpKey(bp.first, bp.second);
        const auto& Bi = nodesByBlock[bp.first];
        const auto& Bj = (bp.first == bp.second) ? Bi : nodesByBlock[bp.second];
        std::vector<std::pair<int64_t, int64_t>> pairs;
        for (int64_t i = 0; i < cnt; i++) {
            int64_t u = drawStub(Bi, localResidual, rng);
            if (u < 0) break;
            // Decrement before the v draw so v's weights see the same
            // post-pick residual the JS impl does. Defer trace emission
            // until BOTH stubs land: the JS replay reads stub entries
            // strictly in pairs, so an orphan u (with v draw failing)
            // would desync the cursor.
            localResidual[u] -= 1;
            int64_t v = drawStub(Bj, localResidual, rng);
            if (v < 0) {
                localResidual[u] += 1;  // refund
                break;
            }
            localResidual[v] -= 1;
            TraceEntry teU;
            teU.kind = "stub"; teU.pair = bpStr; teU.side = "u"; teU.node = u;
            g_trace.push_back(teU);
            TraceEntry teV;
            teV.kind = "stub"; teV.pair = bpStr; teV.side = "v"; teV.node = v;
            g_trace.push_back(teV);
            pairs.emplace_back(u, v);
        }
        // Fisher-Yates shuffle of pairs (matches JS).
        std::vector<int64_t> j_seq;
        std::uniform_real_distribution<double> dd(0.0, 1.0);
        for (int64_t i = (int64_t)pairs.size() - 1; i > 0; i--) {
            int64_t j = (int64_t)std::floor(dd(rng) * (double)(i + 1));
            j_seq.push_back(j);
            std::swap(pairs[i], pairs[j]);
        }
        TraceEntry shuf;
        shuf.kind = "shuffle"; shuf.pair = bpStr; shuf.j_seq = j_seq;
        g_trace.push_back(shuf);

        validPool[bpStr] = {};
        for (auto& pr : pairs) {
            int64_t u = pr.first, v = pr.second;
            if (u == v) {
                invalidEdges.emplace_back(u, v);
                continue;
            }
            std::string ek = edgeKey(u, v);
            if (validSet.count(ek)) {
                invalidEdges.emplace_back(u, v);
                continue;
            }
            validSet.insert(ek);
            validPool[bpStr].push_back(normEdge(u, v));
        }
    }

    // Phase 2: 2-opt repair (mirrors graph_utils.cluster_preserving_2opt_rewire).
    auto isValidNew = [&](int64_t a, int64_t bb) -> bool {
        if (a == bb) return false;
        std::string ek = edgeKey(a, bb);
        return validSet.count(ek) == 0;
    };
    std::vector<std::pair<int64_t, int64_t>> queue = invalidEdges;
    int maxRetries = 10;
    for (int attempt = 0; attempt < maxRetries && !queue.empty(); attempt++) {
        int64_t lastRecycle = (int64_t)queue.size();
        int64_t recycle = lastRecycle;
        while (!queue.empty()) {
            recycle--;
            if (recycle < 0) {
                if ((int64_t)queue.size() < lastRecycle) {
                    lastRecycle = queue.size();
                    recycle = lastRecycle;
                } else break;
            }
            auto pr = queue.front(); queue.erase(queue.begin());
            int64_t u = pr.first, v = pr.second;
            auto bp = bpOf(fx, u, v);
            std::string bpStr = mkBpKey(bp.first, bp.second);
            auto& pool = validPool[bpStr];
            if (pool.empty()) {
                queue.push_back({u, v});
                continue;
            }
            std::uniform_int_distribution<size_t> ui(0, pool.size() - 1);
            size_t idx = ui(rng);
            auto xy = pool[idx];
            int64_t x = xy.first, y = xy.second;
            int64_t A = bp.first, B = bp.second;
            std::pair<int64_t, int64_t> new1, new2;
            int coin = -1;
            if (A != B) {
                int64_t uA = (fx.b.at(u) == A) ? u : v;
                int64_t uB = (fx.b.at(u) == A) ? v : u;
                int64_t xA = (fx.b.at(x) == A) ? x : y;
                int64_t xB = (fx.b.at(x) == A) ? y : x;
                new1 = normEdge(uA, xB);
                new2 = normEdge(xA, uB);
            } else {
                std::uniform_real_distribution<double> dd(0.0, 1.0);
                double rv = dd(rng);
                coin = (rv < 0.5) ? 0 : 1;
                if (coin == 0) {
                    new1 = normEdge(u, x); new2 = normEdge(v, y);
                } else {
                    new1 = normEdge(u, y); new2 = normEdge(v, x);
                }
            }
            TraceEntry rep;
            rep.kind = "repair"; rep.attempt = attempt; rep.u = u; rep.v = v;
            rep.bp = bpStr; rep.idx = (int64_t)idx; rep.coin = coin;
            g_trace.push_back(rep);

            std::string k1 = edgeKey(new1.first, new1.second);
            std::string k2 = edgeKey(new2.first, new2.second);
            if (isValidNew(new1.first, new1.second)
                && isValidNew(new2.first, new2.second)
                && k1 != k2) {
                validSet.erase(edgeKey(x, y));
                pool[idx] = pool.back();
                pool.pop_back();
                validSet.insert(k1);
                validSet.insert(k2);
                pool.push_back(new1);
                pool.push_back(new2);
            } else {
                queue.push_back({u, v});
            }
        }
    }

    // Phase 3: commit. Walk validPool in sorted-key order; drop pairs that
    // collide with exist_neighbor.
    std::vector<std::pair<int64_t, int64_t>> placed;
    std::vector<std::string> poolKeys;
    for (auto& kv : validPool) poolKeys.push_back(kv.first);
    std::sort(poolKeys.begin(), poolKeys.end());
    for (auto& k : poolKeys) {
        for (auto& e : validPool[k]) {
            int64_t a = e.first, b = e.second;
            if (fx.existNeighbor[a].count(b) || fx.existNeighbor[b].count(a)) continue;
            placed.push_back({a, b});
            fx.existNeighbor[a].insert(b);
            fx.existNeighbor[b].insert(a);
        }
    }

    // Emit JSON.
    std::ostringstream o;
    o << "{\"edges\":[";
    for (size_t i = 0; i < placed.size(); i++) {
        if (i) o << ",";
        o << "[" << placed[i].first << "," << placed[i].second << "]";
    }
    o << "],\"trace\":[";
    for (size_t i = 0; i < g_trace.size(); i++) {
        if (i) o << ",";
        const auto& t = g_trace[i];
        o << "{\"kind\":\"" << t.kind << "\"";
        if (t.kind == "stub") {
            o << ",\"pair\":\"" << t.pair << "\""
              << ",\"side\":\"" << t.side << "\""
              << ",\"node\":" << t.node;
        } else if (t.kind == "shuffle") {
            o << ",\"pair\":\"" << t.pair << "\",\"j_seq\":[";
            for (size_t j = 0; j < t.j_seq.size(); j++) {
                if (j) o << ",";
                o << t.j_seq[j];
            }
            o << "]";
        } else if (t.kind == "repair") {
            o << ",\"attempt\":" << t.attempt
              << ",\"u\":" << t.u << ",\"v\":" << t.v
              << ",\"bp\":\"" << t.bp << "\""
              << ",\"idx\":" << t.idx
              << ",\"coin\":";
            if (t.coin < 0) o << "null"; else o << t.coin;
        }
        o << "}";
    }
    o << "]}\n";
    std::cout << o.str();
    return 0;
}
