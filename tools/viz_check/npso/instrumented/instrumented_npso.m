function instrumented_npso(jobfile, outfile)
% Instrumented driver for nPSO_model. Reads a JSON job from jobfile,
% writes a JSON blob to outfile containing canonical edges + comm + a
% trace of every random draw the algorithm made.
%
% Job fields:
%   N (int), m (int), T (double), gamma (double),
%   C (int), model (string: 'nPSO2'), weights (vector len C),
%   seed (int), npso_dir (string).
%
% Output fields:
%   canonical_edges (Nx2 int matrix), canonical_comm (Nx1 int),
%   instr_edges, instr_comm,
%   match (bool: instrumented run matches canonical),
%   trace.angles (Nx1 double),
%   trace.picks (cell of length N-m-1 int row vectors),
%   N, m, T, gamma, C, mu (1xC double, cluster centers).

job = jsondecode(fileread(jobfile));
addpath(job.npso_dir);

N = double(job.N);
m = double(job.m);
T = double(job.T);
gamma = double(job.gamma);
C = double(job.C);
weights = double(job.weights(:))';
seed = double(job.seed);

if ~strcmpi(job.model, 'nPSO2')
    error('Only nPSO2 supported in instrumented harness; got %s', job.model);
end

mu_row = (0:C-1) .* (2*pi/C);
sigma_sq = (2*pi/C/6)^2;
p = weights ./ sum(weights);
distr = gmdistribution(mu_row', sigma_sq, p);

% Canonical run.
rng(seed);
[adj_c, ~, comm_c, ~] = nPSO_model(N, m, T, gamma, distr, 0);
edges_c = adj_to_edges(adj_c);

% Instrumented run from a fresh seed.
rng(seed);
[adj_i, comm_i, trace] = nPSO_model_inst(N, m, T, gamma, distr);
edges_i = adj_to_edges(adj_i);

out = struct();
out.canonical_edges = edges_c;
out.canonical_comm = comm_c(:)';
out.instr_edges = edges_i;
out.instr_comm = comm_i(:)';
out.match = isequal(sortrows(edges_c), sortrows(edges_i)) && ...
            isequal(comm_c(:), comm_i(:));
out.trace = trace;
out.N = N; out.m = m; out.T = T; out.gamma = gamma; out.C = C;
out.mu = mu_row;

fid = fopen(outfile, 'w');
fprintf(fid, '%s', jsonencode(out));
fclose(fid);
end


function edges = adj_to_edges(adj)
[u_list, v_list] = find(triu(adj, 1));
edges = double([u_list, v_list]);
end


function [adj, comm, trace] = nPSO_model_inst(N, m, T, gamma, distr)
% Direct port of externals/npso/nPSO_model.m for the gmdistribution path
% with logging hooks at every random call site. RNG state advances
% identically to the canonical sampler (we delegate to the same `random`
% and `datasample` calls).

coords = zeros(N, 2);
beta = 1 / (gamma - 1);
x = zeros(m*(m+1)/2 + (N-m-1)*m, 2);
i = 0;

trace = struct();
trace.angles = [];
trace.picks = {};

gmd = distr;
C = gmd.NumComponents;
mu = gmd.mu';
coords(:,1) = mod(random(gmd, N), 2*pi);
trace.angles = coords(:,1);
[~, comm] = min(pi - abs(pi - abs(repmat(coords(:,1),1,C) - repmat(mu,N,1))), [], 2);

pick_count = 0;
for t = 2:N
    coords(1:t-1, 2) = beta .* (2*log(1:t-1)) + (1-beta)*2*log(t);
    coords(t, 2) = 2*log(t);

    if t-1 <= m
        x(i+1:i+t-1, 1) = t;
        x(i+1:i+t-1, 2) = 1:t-1;
        i = i + t-1;
    else
        d = pdist2(coords(t,:), coords(1:t-1,:), @hyperbolic_dist_local);
        if T == 0
            [~, idx] = sort(d);
            x(i+1:i+m, 1) = t;
            x(i+1:i+m, 2) = idx(1:m);
            i = i + m;
        else
            if beta == 1
                Rt = 2*log(t) - 2*log((2*T*log(t))/(sin(T*pi)*m));
            else
                Rt = 2*log(t) - 2*log((2*T*(1 - exp(-(1 - beta)*log(t))))/(sin(T*pi)*m*(1 - beta)));
            end
            p = 1 ./ (1 + exp((d - Rt) ./ (2*T)));
            idx = datasample(1:t-1, m, 'Replace', false, 'Weights', p);
            pick_count = pick_count + 1;
            trace.picks{pick_count} = idx;
            x(i+1:i+m, 1) = t;
            x(i+1:i+m, 2) = idx;
            i = i + m;
        end
    end
end

adj = sparse([x(:,1); x(:,2)], [x(:,2); x(:,1)], 1, N, N);
end


function d = hyperbolic_dist_local(XI, XJ)
A = pi - abs(pi - abs(XI(1) - XJ(:,1)));
d = acosh(cosh(XI(2)).*cosh(XJ(:,2)) - sinh(XI(2)).*sinh(XJ(:,2)).*cos(A));
d(isinf(d)) = 0;
if ~isreal(d)
    d(imag(d)~=0) = abs(XI(2) - XJ(imag(d)~=0, 2));
end
end
