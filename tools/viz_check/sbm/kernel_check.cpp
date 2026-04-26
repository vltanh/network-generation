// Standalone reference kernel mirroring graph-tool's gen_sbm.hh (templated
// micro_deg=true, undirected). Reads a JSON fixture from stdin, writes a
// JSON trace + edges to stdout. Build:
//
//   g++ -std=c++17 -O2 -o /tmp/sbm_kernel_check tools/sbm/kernel_check.cpp
//
// Run:
//   echo FIXTURE_JSON | /tmp/sbm_kernel_check
//
// The kernel is a verbatim port of the C++ template gen_sbm in
// graph-tool/src/graph/generation/graph_sbm.hh, restricted to the path
// {undirected, micro_ers=true, micro_degs=true} that src/sbm/gen.py uses.
// PRNG is std::mt19937, seeded by the JSON's "seed" field. graph-tool's
// kernel uses boost::random::mt19937 internally so the per-pair edge
// counts match exactly but the per-edge endpoints differ.
//
// Output JSON shape:
//   { "fixture": <echo>,
//     "pairs":   [{ "r":, "s":, "mrs":, "ers": }, ...],
//     "trace":   [{ "step": k, "r":, "s":,
//                   "urnR_size": .., "i_a":,
//                   "urnS_size": .., "i_b":,
//                   "u":, "v": }, ...],
//     "edges":   [[u, v], ...],
//     "achieved_degree": [...],
//     "achieved_e_rs":   [[r, s, count], ...] }
#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <iostream>
#include <map>
#include <random>
#include <sstream>
#include <string>
#include <vector>

// ----- minimal JSON (input) -----------------------------------------------
// Parses just enough for the fixture shape we feed in. Whitespace tolerant.
struct JParse {
    const std::string& s;
    size_t p = 0;
    explicit JParse(const std::string& src) : s(src) {}
    void skip() { while (p < s.size() && std::isspace((unsigned char)s[p])) p++; }
    char peek() { skip(); return p < s.size() ? s[p] : '\0'; }
    char eat() { skip(); return s[p++]; }
    void expect(char c) { skip(); if (s[p++] != c) throw std::runtime_error("expected " + std::string(1, c)); }
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
    std::vector<int64_t> readIntArr() {
        std::vector<int64_t> v; expect('[');
        while (peek() != ']') { v.push_back(readInt()); if (peek() == ',') eat(); }
        expect(']'); return v;
    }
    std::vector<std::vector<int64_t>> readIntMat() {
        std::vector<std::vector<int64_t>> v; expect('[');
        while (peek() != ']') { v.push_back(readIntArr()); if (peek() == ',') eat(); }
        expect(']'); return v;
    }
};

// ----- urn sampler (without replacement, matches UrnSampler<_,false>) ----
template <class V, class RNG>
V popUniform(std::vector<V>& urn, RNG& rng) {
    std::uniform_int_distribution<size_t> d(0, urn.size() - 1);
    size_t i = d(rng);
    std::swap(urn[i], urn.back());
    V v = urn.back();
    urn.pop_back();
    return v;
}

// peek-then-commit version so the trace can record `i` before the pop.
template <class V, class RNG>
std::pair<V, size_t> popUniformLogged(std::vector<V>& urn, RNG& rng) {
    std::uniform_int_distribution<size_t> d(0, urn.size() - 1);
    size_t i = d(rng);
    V v = urn[i];
    urn[i] = urn.back();
    urn.pop_back();
    return {v, i};
}

int main() {
    std::stringstream ss;
    ss << std::cin.rdbuf();
    std::string src = ss.str();
    JParse jp(src);

    std::vector<int64_t> blocks, degrees;
    std::vector<std::vector<int64_t>> e_rs_triples;
    int64_t seed = 1;
    int64_t num_blocks = 0;
    bool dbg = false;

    jp.expect('{');
    while (jp.peek() != '}') {
        std::string k = jp.readKey();
        if (k == "blocks") blocks = jp.readIntArr();
        else if (k == "degrees") degrees = jp.readIntArr();
        else if (k == "e_rs") e_rs_triples = jp.readIntMat();
        else if (k == "seed") seed = jp.readInt();
        else if (k == "num_blocks") num_blocks = jp.readInt();
        else if (k == "debug") { jp.skip(); jp.expect('1'); dbg = true; }
        else throw std::runtime_error("unexpected key " + k);
        if (jp.peek() == ',') jp.eat();
    }
    jp.expect('}');

    size_t N = blocks.size();
    if (num_blocks == 0) {
        for (auto b : blocks) num_blocks = std::max<int64_t>(num_blocks, b + 1);
    }

    // Build per-block urns of stub-copies. UrnSampler holds a copy of the
    // input for each stub; we use vertex-id directly.
    std::vector<std::vector<int64_t>> urns(num_blocks);
    for (size_t v = 0; v < N; ++v) {
        int64_t r = blocks[v];
        for (int64_t k = 0; k < degrees[v]; ++k) urns[r].push_back((int64_t)v);
    }

    // Pair iteration: row-major upper triangle (r <= s) over nonzero e_rs.
    // The Python wrapper extracts probs.nonzero() in CSR order, then filters
    // r <= s. We mirror that by sorting incoming triples by (r, s) and
    // dropping the lower triangle.
    std::vector<std::array<int64_t, 3>> pairs;
    for (auto& t : e_rs_triples) {
        if (t.size() != 3) throw std::runtime_error("bad e_rs triple");
        if (t[0] > t[1]) continue;
        pairs.push_back({t[0], t[1], t[2]});
    }
    std::sort(pairs.begin(), pairs.end(),
              [](const std::array<int64_t, 3>& a, const std::array<int64_t, 3>& b) {
                  if (a[0] != b[0]) return a[0] < b[0];
                  return a[1] < b[1];
              });

    std::mt19937 rng((uint32_t)seed);

    std::vector<std::vector<int64_t>> trace;     // [step, r, s, urnR_sz, ia, urnS_sz, ib, u, v]
    std::vector<std::pair<int64_t, int64_t>> edges;
    std::vector<int64_t> ach_deg(N, 0);
    std::map<std::pair<int64_t, int64_t>, int64_t> ach_e_rs;
    std::vector<std::array<int64_t, 4>> pair_log; // {r, s, mrs, ers}

    int64_t step = 0;
    for (auto& pr : pairs) {
        int64_t r = pr[0], s = pr[1], p = pr[2];
        if (p == 0) continue;
        int64_t mrs = (r == s) ? p / 2 : p;
        int64_t ers = (r != s) ? mrs : 2 * mrs;
        pair_log.push_back({r, s, mrs, ers});

        // has_n consistency check (mirrors UrnSampler::has_n with replacement=false).
        if ((int64_t)urns[r].size() < ers) {
            std::cerr << "FAIL has_n on urn r=" << r << " size=" << urns[r].size() << " < ers=" << ers << "\n";
            return 1;
        }
        if (r != s && (int64_t)urns[s].size() < ers) {
            std::cerr << "FAIL has_n on urn s=" << s << " size=" << urns[s].size() << " < ers=" << ers << "\n";
            return 1;
        }

        for (int64_t j = 0; j < mrs; ++j) {
            size_t ia, ib;
            int64_t u, v;
            int64_t r_size_before = urns[r].size();
            int64_t s_size_before = (r == s) ? urns[r].size() : urns[s].size();
            if (r == s) {
                auto [a, ia_] = popUniformLogged(urns[r], rng);
                auto [b, ib_] = popUniformLogged(urns[r], rng);
                u = a; v = b; ia = ia_; ib = ib_;
            } else {
                auto [a, ia_] = popUniformLogged(urns[r], rng);
                auto [b, ib_] = popUniformLogged(urns[s], rng);
                u = a; v = b; ia = ia_; ib = ib_;
                // For r != s after first pop, the second urn sees an unchanged
                // population so s_size_before is still the pre-call s urn size.
            }
            edges.emplace_back(u, v);
            ach_deg[u]++; ach_deg[v]++;
            int64_t lo = std::min(u, v), hi = std::max(u, v);
            int64_t br = blocks[lo], bs = blocks[hi];
            if (br > bs) std::swap(br, bs);
            ach_e_rs[{br, bs}] += (br == bs) ? 2 : 1;

            trace.push_back({step, r, s, r_size_before, (int64_t)ia, s_size_before, (int64_t)ib, u, v});

            if (dbg) {
                std::fprintf(stderr,
                    "step=%ld pair=(%ld,%ld) urn_r=%ld i=%ld urn_s=%ld j=%ld -> (%ld,%ld)\n",
                    (long)step, (long)r, (long)s,
                    (long)r_size_before, (long)ia,
                    (long)s_size_before, (long)ib,
                    (long)u, (long)v);
            }
            step++;
        }
    }

    // Emit JSON.
    std::ostringstream o;
    o << "{\"seed\":" << seed
      << ",\"num_blocks\":" << num_blocks
      << ",\"pairs\":[";
    for (size_t i = 0; i < pair_log.size(); ++i) {
        if (i) o << ",";
        o << "{\"r\":" << pair_log[i][0] << ",\"s\":" << pair_log[i][1]
          << ",\"mrs\":" << pair_log[i][2] << ",\"ers\":" << pair_log[i][3] << "}";
    }
    o << "],\"trace\":[";
    for (size_t i = 0; i < trace.size(); ++i) {
        if (i) o << ",";
        const auto& t = trace[i];
        o << "{\"step\":" << t[0]
          << ",\"r\":"      << t[1]
          << ",\"s\":"      << t[2]
          << ",\"urnR\":"   << t[3]
          << ",\"i_a\":"    << t[4]
          << ",\"urnS\":"   << t[5]
          << ",\"i_b\":"    << t[6]
          << ",\"u\":"      << t[7]
          << ",\"v\":"      << t[8] << "}";
    }
    o << "],\"edges\":[";
    for (size_t i = 0; i < edges.size(); ++i) {
        if (i) o << ",";
        o << "[" << edges[i].first << "," << edges[i].second << "]";
    }
    o << "],\"achieved_degree\":[";
    for (size_t i = 0; i < ach_deg.size(); ++i) {
        if (i) o << ",";
        o << ach_deg[i];
    }
    o << "],\"achieved_e_rs\":[";
    bool first = true;
    for (auto& kv : ach_e_rs) {
        if (!first) o << ",";
        first = false;
        o << "[" << kv.first.first << "," << kv.first.second << "," << kv.second << "]";
    }
    o << "]}\n";

    std::cout << o.str();
    return 0;
}
