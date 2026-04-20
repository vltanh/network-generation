function [edges, comm] = run_npso(N, m, T, gamma, c, output_prefix, seed)
    if nargin >= 7
        rng(seed);
    end
    [adj, ~, comm, ~] = nPSO_model(N, m, T, gamma, c, 0);

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
