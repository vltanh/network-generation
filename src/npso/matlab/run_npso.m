function [edges, comm] = run_npso(N, m, T, gamma, C, model, weights, output_prefix, seed)
    if nargin >= 9
        rng(seed);
    end

    distr = build_distr(C, model, weights);
    [adj, ~, comm, ~] = nPSO_model(N, m, T, gamma, distr, 0);

    comm = double(comm(:));

    if nargout == 0
        % Subprocess path — byte-compat with the legacy contract.
        % Outer-u inner-v scan produces the same edge.tsv ordering the
        % Python SubprocessRunner read historically; keep it to preserve
        % hashes of committed reference outputs.
        network_file_output_path = strcat(output_prefix, 'edge.tsv');
        fid = fopen(network_file_output_path, 'w');
        for u = 1:N
            for v = u+1:N
                if adj(u, v) == 1
                    fprintf(fid, '%d\t%d\n', u, v);
                end
            end
        end
        fclose(fid);

        clustering_file_output_path = strcat(output_prefix, 'com.tsv');
        fid = fopen(clustering_file_output_path, 'w');
        for i = 1:N
            fprintf(fid, '%d\t%d\n', i, comm(i));
        end
        fclose(fid);
        return;
    end

    % Engine path — in-memory return. Edge list from triu(adj, 1).
    % MATLAB's find() is column-major so pairs come back sorted by v
    % ascending, then u ascending within each v. gen.py relies on this
    % order being deterministic across runs, not on matching the
    % subprocess path's order.
    [u_list, v_list] = find(triu(adj, 1));
    edges = double([u_list, v_list]);
end


function distr = build_distr(C, model, weights)
    % Maps the three paper variants to the `distr` arg accepted by
    % upstream nPSO_model.
    %   nPSO1: integer C triggers the paper's default GMM (equal ρ_k).
    %   nPSO2: gmdistribution with caller-supplied weights.
    %   nPSO3: Gaussian/Gamma mixture built by the upstream helper.
    switch upper(model)
        case 'NPSO1'
            distr = C;
        case 'NPSO2'
            if isempty(weights) || numel(weights) ~= C
                error(['nPSO2 requires %d mixing proportions, got %d. ', ...
                       'Pass the weights vector via run_npso(...).'], ...
                      C, numel(weights));
            end
            mu = (0:C-1) .* (2*pi/C);
            sigma_sq = (2*pi/C/6)^2;
            p = weights(:)';
            p = p ./ sum(p);
            distr = gmdistribution(mu', sigma_sq, p);
        case 'NPSO3'
            distr = create_mixture_gaussian_gamma_pdf(C);
        otherwise
            error('Unknown model: %s. Expected nPSO1, nPSO2, or nPSO3.', model);
    end
end
