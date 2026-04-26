# Instrumented ABCD sampler driver.
#
# Re-implements ABCDGraphGenerator.populate_clusters + config_model with a
# logging hook at every random call site. Records each draw's resolved value
# so a JS replay can reproduce the canonical output without replicating
# Julia's StatsBase / Random internals. The canonical sampler is also run
# in the same process and its (sorted) edge set is compared against the
# instrumented run as a sanity check that the port has not drifted.
#
# CLI: reads a JSON-ish job blob on stdin (no JSON dep -> hand-parsed):
#   {"deg_file": "...", "cs_file": "...", "xi": 0.4, "seed": 1, "n_outliers": 0}
#
# Writes JSON-ish output on stdout:
#   {"canonical_edges": [[a,b],...],
#    "canonical_clusters": [...],
#    "instr_edges": [[a,b],...],
#    "instr_clusters": [...],
#    "match_canonical": true,
#    "trace": [{...},...]}

using Pkg
Pkg.activate(joinpath(@__DIR__, "..", "..", "..", "..", "externals", "abcd"); io=devnull)

using ABCDGraphGenerator
using Random
using StatsBase

# ---------------------------------------------------------------------------
# Tiny JSON encoder. Just enough for our value shapes (numbers, strings,
# bools, vectors, tuples, dicts with string keys).
# ---------------------------------------------------------------------------

function _j(s::AbstractString)
    buf = IOBuffer()
    print(buf, '"')
    for c in s
        if c == '"'
            print(buf, "\\\"")
        elseif c == '\\'
            print(buf, "\\\\")
        elseif c == '\n'
            print(buf, "\\n")
        elseif c == '\r'
            print(buf, "\\r")
        elseif c == '\t'
            print(buf, "\\t")
        else
            print(buf, c)
        end
    end
    print(buf, '"')
    return String(take!(buf))
end

_j(b::Bool) = b ? "true" : "false"
_j(x::Integer) = string(x)
_j(x::AbstractFloat) = isfinite(x) ? repr(x) : "null"
_j(::Nothing) = "null"
_j(v::AbstractVector) = "[" * join(_j.(v), ",") * "]"
_j(v::Tuple) = "[" * join(_j.(collect(v)), ",") * "]"
_j(v::AbstractSet) = "[" * join(_j.(collect(v)), ",") * "]"

function _j(d::AbstractDict)
    parts = String[]
    # Stable order: sort keys lexicographically so output is reproducible.
    for k in sort!(collect(keys(d)))
        push!(parts, _j(string(k)) * ":" * _j(d[k]))
    end
    return "{" * join(parts, ",") * "}"
end

# ---------------------------------------------------------------------------
# Hand-rolled minimal stdin job parser. Accepts a single JSON object with
# string keys mapping to Number/String values. Avoids pulling JSON.jl.
# ---------------------------------------------------------------------------

function _strip_quotes(s)
    s = strip(s)
    if startswith(s, '"') && endswith(s, '"')
        return s[2:end-1]
    end
    return s
end

function parse_job(text::AbstractString)
    # Strip outer braces.
    t = strip(text)
    @assert startswith(t, "{") && endswith(t, "}")
    inner = strip(t[2:end-1])
    out = Dict{String, Any}()
    # Split by top-level commas. Job blobs are flat (no nested objects), so
    # a naive split is fine.
    for piece in split(inner, ',')
        kv = split(piece, ':'; limit=2)
        length(kv) == 2 || continue
        k = _strip_quotes(kv[1])
        vs = strip(kv[2])
        if startswith(vs, '"')
            out[k] = _strip_quotes(vs)
        elseif vs in ("true", "false")
            out[k] = (vs == "true")
        else
            # Try int then float.
            n = tryparse(Int, vs)
            if n !== nothing
                out[k] = n
            else
                f = tryparse(Float64, vs)
                @assert f !== nothing "could not parse value $(vs) for $(k)"
                out[k] = f
            end
        end
    end
    return out
end

# ---------------------------------------------------------------------------
# Instrumented port of populate_clusters + config_model.
#
# Mirrors externals/abcd/src/graph_sampler.jl line-by-line, with logging
# inserted at each rand / sample / shuffle / randround / rand(::Set) site.
# Logged value is the RESOLVED outcome of the call (e.g. shuffled vector,
# chosen index, picked tuple), so the JS port can consume it without
# re-running Julia's internals.
# ---------------------------------------------------------------------------

mutable struct Tracer
    entries::Vector{Dict{String, Any}}
end
Tracer() = Tracer(Dict{String, Any}[])

function log!(tr::Tracer, site::String; kwargs...)
    d = Dict{String, Any}("site" => site)
    for (k, v) in kwargs
        d[String(k)] = v
    end
    push!(tr.entries, d)
end

# randround_log: replicates ABCDGraphGenerator.randround with logging.
function randround_log!(tr::Tracer, x; ctx::String="")
    d = floor(Int, x)
    r = rand()
    val = d + (r < x - d ? 1 : 0)
    log!(tr, "randround"; ctx=ctx, x=Float64(x), draw=r, value=val)
    return val
end

# shuffle_log: defer to Random.shuffle! to match canonical RNG cost (Julia's
# shuffle! uses ltm52 rejection sampling, not raw rand(1:i), so reimplementing
# Fisher-Yates would consume a different number of rand() calls and drift).
# We shuffle an index array (1:n), then permute the input, and log the perm.
function shuffle_log!(tr::Tracer, arr::AbstractVector; ctx::String="")
    n = length(arr)
    if n <= 1
        log!(tr, "shuffle"; ctx=ctx, n=n, perm=collect(1:n), after=copy(arr))
        return arr
    end
    perm = collect(1:n)
    Random.shuffle!(perm)
    src = copy(arr)
    for k in 1:n
        arr[k] = src[perm[k]]
    end
    log!(tr, "shuffle"; ctx=ctx, n=n, perm=perm, after=copy(arr))
    return arr
end

# sample_outlier_log: wraps StatsBase.sample(idx:n, nout, replace=false).
# Records the chosen indices (already canonical-ordered).
function sample_outlier_log!(tr::Tracer, range::AbstractUnitRange, nout::Int)
    result = sample(range, nout, replace=false)
    log!(tr, "outlier_sample"; lo=first(range), hi=last(range),
         nout=nout, picked=collect(result))
    return result
end

# sample_assign_log: wraps weighted single sample for vertex->cluster.
function sample_assign_log!(tr::Tracer, range::AbstractUnitRange, wts; ctx::String="")
    result = sample(range, wts)
    log!(tr, "vertex_assign"; ctx=ctx, lo=first(range), hi=last(range),
         picked=result)
    return result
end

# uniform_log: wraps a bare rand() draw.
function uniform_log!(tr::Tracer; ctx::String="")
    r = rand()
    log!(tr, "uniform"; ctx=ctx, value=r)
    return r
end

# uniform_int_log: wraps rand(axes).
function uniform_int_log!(tr::Tracer, range; ctx::String="")
    v = rand(range)
    log!(tr, "uniform_int"; ctx=ctx, lo=first(range), hi=last(range), value=v)
    return v
end

# rand_set_log: wraps rand(::AbstractSet). Logs the element actually picked.
function rand_set_log!(tr::Tracer, s::AbstractSet; ctx::String="")
    e = rand(s)
    log!(tr, "rand_set"; ctx=ctx, size=length(s), element=collect(e))
    return e
end

# ---------------------------------------------------------------------------
# Instrumented populate_clusters. Direct port of canonical with logging.
# ---------------------------------------------------------------------------

function populate_clusters_inst(params::ABCDGraphGenerator.ABCDParams, tr::Tracer)
    w, s = params.w, params.s
    if isnothing(params.ξ)
        mul = 1.0 - params.μ
    else
        n = length(w)
        if params.hasoutliers
            s0 = s[1]
            n = length(params.w)
            ϕ = 1.0 - sum((sl/(n-s0))^2 for sl in s[2:end]) * (n-s0)*params.ξ / ((n-s0)*params.ξ + s0)
        else
            ϕ = 1.0 - sum((sl/n)^2 for sl in s)
        end
        mul = 1.0 - params.ξ*ϕ
    end
    @assert length(w) == sum(s)
    @assert 0 ≤ mul ≤ 1
    @assert issorted(w, rev=true)
    if params.hasoutliers
        @assert issorted(s[2:end], rev=true)
    else
        @assert issorted(s, rev=true)
    end

    slots = copy(s)
    clusters = fill(-1, length(w))

    if params.hasoutliers
        nout = s[1]
        n = length(params.w)
        L = sum(d -> min(1.0, params.ξ * d), params.w)
        threshold = L + nout - L * nout / n - 1.0
        idx = findfirst(<=(threshold), params.w)
        @assert all(i -> params.w[i] <= threshold, idx:n)
        if length(idx:n) < nout
            throw(ArgumentError("not enough nodes feasible for classification as outliers"))
        end
        tabu = sample_outlier_log!(tr, idx:n, nout)
        clusters[tabu] .= 1
        slots[1] = 0
        stabu = Set(tabu)
    else
        stabu = Set{Int}()
    end

    j0 = params.hasoutliers ? 1 : 0
    j = j0
    tmp_wsum = 0
    bad_weights = Int[]
    for (i, vw) in enumerate(w)
        i in stabu && continue

        # Walk j forward to first cluster with at least one slot for vw.
        while j + 1 ≤ length(s) && tmp_wsum == 0
            if mul * vw + 1 > s[j+1]
                push!(bad_weights, vw)
            end
            j += 1
            tmp_wsum += slots[j]
        end

        while j + 1 ≤ length(s) && mul * vw + 1 ≤ s[j + 1]
            j += 1
            tmp_wsum += slots[j]
        end

        j == j0 && throw(ArgumentError("could not find a large enough cluster for vertex of weight $vw"))
        wts = Weights(view(slots, (j0+1):j))
        wts.sum == 0 && throw(ArgumentError("could not find an empty slot for vertex of weight $vw"))
        @assert wts.sum == tmp_wsum

        loc = sample_assign_log!(tr, (j0+1):j, wts; ctx="i=$i")
        clusters[i] = loc
        slots[loc] -= 1
        tmp_wsum -= 1
    end

    @assert sum(slots) == 0
    @assert minimum(clusters) == 1
    return clusters
end

# ---------------------------------------------------------------------------
# Instrumented config_model. Direct port with logging at every rand site.
# ---------------------------------------------------------------------------

function config_model_inst(clusters, params::ABCDGraphGenerator.ABCDParams, tr::Tracer)
    @assert !params.isCL
    @assert !params.islocal
    w, s, μ = params.w, params.s, params.μ

    cluster_weight = zeros(Int, length(s))
    for i in axes(w, 1)
        cluster_weight[clusters[i]] += w[i]
    end
    total_weight = sum(cluster_weight)
    if isnothing(params.ξ)
        @assert !params.hasoutliers
        ξg = μ / (1.0 - sum(x -> x^2, cluster_weight) / total_weight^2)
        ξg >= 1 && throw(ArgumentError("μ is too large to generate a graph"))
    else
        ξg = params.ξ
    end
    w_internal_raw = [w[i] * (1 - ξg) for i in axes(w, 1)]
    if params.hasoutliers
        for i in findall(==(1), clusters)
            w_internal_raw[i] = 0
        end
    end

    clusterlist = [Int[] for i in axes(s, 1)]
    for i in axes(clusters, 1)
        push!(clusterlist[clusters[i]], i)
    end

    edges = Set{Tuple{Int, Int}}()

    unresolved_collisions = 0
    w_internal = zeros(Int, length(w_internal_raw))

    for (cidx, cluster) in enumerate(clusterlist)
        maxw_idx = argmax(view(w_internal_raw, cluster))
        wsum = 0
        for i in axes(cluster, 1)
            if i != maxw_idx
                neww = randround_log!(tr, w_internal_raw[cluster[i]];
                                       ctx="cluster=$cidx,i=$i")
                w_internal[cluster[i]] = neww
                wsum += neww
            end
        end
        maxw = floor(Int, w_internal_raw[cluster[maxw_idx]])
        w_internal[cluster[maxw_idx]] = maxw + (isodd(wsum) ? iseven(maxw) : isodd(maxw))
        if w_internal[cluster[maxw_idx]] > w[cluster[maxw_idx]]
            @assert w[cluster[maxw_idx]] + 1 == w_internal[cluster[maxw_idx]]
            w[cluster[maxw_idx]] += 1
        end

        if params.hasoutliers && cluster === clusterlist[1]
            @assert findall(clusters .== 1) == cluster
            @assert all(iszero, w_internal[cluster])
        end

        stubs = Int[]
        for i in cluster
            for j in 1:w_internal[i]
                push!(stubs, i)
            end
        end
        @assert sum(w_internal[cluster]) == length(stubs)
        @assert iseven(length(stubs))
        if params.hasoutliers && cluster === clusterlist[1]
            @assert isempty(stubs)
        end

        shuffle_log!(tr, stubs; ctx="cluster=$cidx,kind=local")

        local_edges = Set{Tuple{Int, Int}}()
        recycle = Tuple{Int,Int}[]
        for i in 1:2:length(stubs)
            e = minmax(stubs[i], stubs[i+1])
            if (e[1] == e[2]) || (e in local_edges)
                push!(recycle, e)
            else
                push!(local_edges, e)
            end
        end

        last_recycle = length(recycle)
        recycle_counter = last_recycle
        while !isempty(recycle)
            recycle_counter -= 1
            if recycle_counter < 0
                if length(recycle) < last_recycle
                    last_recycle = length(recycle)
                    recycle_counter = last_recycle
                else
                    break
                end
            end
            p1 = popfirst!(recycle)
            from_recycle = 2 * length(recycle) / length(stubs)
            success = false
            if !(isempty(recycle) && isempty(local_edges))
                for _ in 1:2:length(stubs)
                    coin1 = uniform_log!(tr; ctx="cluster=$cidx,coin=src")
                    p2 = if coin1 < from_recycle || isempty(local_edges)
                        used_recycle = true
                        recycle_idx = uniform_int_log!(tr, axes(recycle, 1);
                                                        ctx="cluster=$cidx,kind=local_recycle")
                        recycle[recycle_idx]
                    else
                        used_recycle = false
                        rand_set_log!(tr, local_edges; ctx="cluster=$cidx,kind=local_edges")
                    end
                    coin2 = uniform_log!(tr; ctx="cluster=$cidx,coin=swap")
                    if coin2 < 0.5
                        newp1 = minmax(p1[1], p2[1])
                        newp2 = minmax(p1[2], p2[2])
                    else
                        newp1 = minmax(p1[1], p2[2])
                        newp2 = minmax(p1[2], p2[1])
                    end
                    if newp1 == newp2
                        good_choice = false
                    elseif (newp1[1] == newp1[2]) || (newp1 in local_edges)
                        good_choice = false
                    elseif (newp2[1] == newp2[2]) || (newp2 in local_edges)
                        good_choice = false
                    else
                        good_choice = true
                    end
                    if good_choice
                        if used_recycle
                            recycle[recycle_idx], recycle[end] = recycle[end], recycle[recycle_idx]
                            pop!(recycle)
                        else
                            pop!(local_edges, p2)
                        end
                        success = true
                        push!(local_edges, newp1)
                        push!(local_edges, newp2)
                        break
                    end
                end
            end
            success || push!(recycle, p1)
        end
        old_len = length(edges)
        union!(edges, local_edges)
        @assert length(edges) == old_len + length(local_edges)
        @assert 2 * (length(local_edges) + length(recycle)) == length(stubs)
        for (a, b) in recycle
            w_internal[a] -= 1
            w_internal[b] -= 1
        end
        unresolved_collisions += length(recycle)
    end

    if unresolved_collisions > 0
        # Stay silent in instrumented mode; canonical log is preserved by the
        # canonical run we also do in this process.
    end

    stubs = Int[]
    for i in axes(w, 1)
        for j in w_internal[i]+1:w[i]
            push!(stubs, i)
        end
    end
    @assert sum(w) == length(stubs) + sum(w_internal)
    if params.hasoutliers
        if 2 * sum(w[clusters .== 1]) > length(stubs)
            # Outlier-lift warning would fire in canonical; we don't echo.
        end
    end
    shuffle_log!(tr, stubs; ctx="kind=global")
    if isodd(length(stubs))
        maxi = 1
        @assert w[stubs[maxi]] > w_internal[stubs[maxi]]
        for i in 2:length(stubs)
            si = stubs[i]
            @assert w[si] > w_internal[si]
            if w[si] > w[stubs[maxi]]
                maxi = i
            end
        end
        si = popat!(stubs, maxi)
        @assert w[si] > w_internal[si]
        w[si] -= 1
    end
    global_edges = Set{Tuple{Int, Int}}()
    recycle = Tuple{Int,Int}[]
    for i in 1:2:length(stubs)
        e = minmax(stubs[i], stubs[i+1])
        if (e[1] == e[2]) || (e in global_edges) || (e in edges)
            push!(recycle, e)
        else
            push!(global_edges, e)
        end
    end
    last_recycle = length(recycle)
    recycle_counter = last_recycle
    while !isempty(recycle)
        recycle_counter -= 1
        if recycle_counter < 0
            if length(recycle) < last_recycle
                last_recycle = length(recycle)
                recycle_counter = last_recycle
            else
                break
            end
        end
        p1 = pop!(recycle)
        from_recycle = 2 * length(recycle) / length(stubs)
        coin1 = uniform_log!(tr; ctx="kind=global,coin=src")
        p2 = if coin1 < from_recycle
            i = uniform_int_log!(tr, axes(recycle, 1);
                                  ctx="kind=global_recycle")
            recycle[i], recycle[end] = recycle[end], recycle[i]
            pop!(recycle)
        else
            x = rand_set_log!(tr, global_edges; ctx="kind=global_edges")
            pop!(global_edges, x)
        end
        coin2 = uniform_log!(tr; ctx="kind=global,coin=swap")
        if coin2 < 0.5
            newp1 = minmax(p1[1], p2[1])
            newp2 = minmax(p1[2], p2[2])
        else
            newp1 = minmax(p1[1], p2[2])
            newp2 = minmax(p1[2], p2[1])
        end
        for newp in (newp1, newp2)
            if (newp[1] == newp[2]) || (newp in global_edges) || (newp in edges)
                push!(recycle, newp)
            else
                push!(global_edges, newp)
            end
        end
    end
    old_len = length(edges)
    union!(edges, global_edges)
    @assert length(edges) == old_len + length(global_edges)
    if isempty(recycle)
        @assert 2 * length(global_edges) == length(stubs)
    else
        last_recycle = length(recycle)
        recycle_counter = last_recycle
        while !isempty(recycle)
            recycle_counter -= 1
            if recycle_counter < 0
                if length(recycle) < last_recycle
                    last_recycle = length(recycle)
                    recycle_counter = last_recycle
                else
                    break
                end
            end
            p1 = pop!(recycle)
            x = rand_set_log!(tr, edges; ctx="kind=final_edges")
            p2 = pop!(edges, x)
            coin = uniform_log!(tr; ctx="kind=final,coin=swap")
            if coin < 0.5
                newp1 = minmax(p1[1], p2[1])
                newp2 = minmax(p1[2], p2[2])
            else
                newp1 = minmax(p1[1], p2[2])
                newp2 = minmax(p1[2], p2[1])
            end
            for newp in (newp1, newp2)
                if (newp[1] == newp[2]) || (newp in edges)
                    push!(recycle, newp)
                else
                    push!(edges, newp)
                end
            end
        end
    end
    return edges
end

function gen_graph_inst(params::ABCDGraphGenerator.ABCDParams, tr::Tracer)
    clusters = populate_clusters_inst(params, tr)
    edges = config_model_inst(clusters, params, tr)
    (edges=edges, clusters=clusters)
end

# ---------------------------------------------------------------------------
# Driver.
# ---------------------------------------------------------------------------

function read_seq_file(path::AbstractString)
    return parse.(Int, readlines(path))
end

function run_canonical(w_in, s_in, ξ, seed, hasoutliers)
    w = copy(w_in)
    s = copy(s_in)
    p = ABCDGraphGenerator.ABCDParams(w, s, nothing, ξ, false, false, hasoutliers)
    Random.seed!(seed)
    out = ABCDGraphGenerator.gen_graph(p)
    return sort(collect(out.edges)), collect(out.clusters)
end

function run_instrumented(w_in, s_in, ξ, seed, hasoutliers)
    w = copy(w_in)
    s = copy(s_in)
    p = ABCDGraphGenerator.ABCDParams(w, s, nothing, ξ, false, false, hasoutliers)
    Random.seed!(seed)
    tr = Tracer()
    out = gen_graph_inst(p, tr)
    return sort(collect(out.edges)), collect(out.clusters), tr.entries
end

function main()
    job_text = read(stdin, String)
    job = parse_job(job_text)

    deg_file = String(job["deg_file"])
    cs_file = String(job["cs_file"])
    ξ = Float64(job["xi"])
    seed = Int(job["seed"])
    n_outliers = Int(job["n_outliers"])

    w = read_seq_file(deg_file)
    s = read_seq_file(cs_file)
    if n_outliers > 0
        n_outliers == s[1] || error("n_outliers ($n_outliers) does not match cs[1] ($(s[1]))")
    end
    hasoutliers = n_outliers > 0

    # Run canonical first (mutates a private copy; doesn't touch the
    # instrumented run's params).
    canonical_edges, canonical_clusters = run_canonical(w, s, ξ, seed, hasoutliers)

    # Run instrumented from a fresh seed.
    instr_edges, instr_clusters, trace = run_instrumented(w, s, ξ, seed, hasoutliers)

    # Sanity: instrumented must produce same edge set + cluster assignment.
    match = (canonical_edges == instr_edges) && (canonical_clusters == instr_clusters)

    out = Dict{String, Any}(
        "canonical_edges" => [collect(e) for e in canonical_edges],
        "canonical_clusters" => canonical_clusters,
        "instr_edges" => [collect(e) for e in instr_edges],
        "instr_clusters" => instr_clusters,
        "match_canonical" => match,
        "trace" => trace,
    )
    println(_j(out))
end

main()
