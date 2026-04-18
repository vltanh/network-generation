function run_npso(N, m, T, gamma, c, output_prefix, seed)
    if nargin >= 7
        rng(seed);
    end
    [adj, coords, comm, d] = nPSO_model(N, m, T, gamma, c, 0);

    network_file_output_path = strcat(output_prefix, 'edge.tsv');
    network_file_output_handle = fopen(network_file_output_path, 'w');
    for u=1:N
        for v=u+1:N
            if adj(u,v) == 1
                fprintf(network_file_output_handle, '%d\t%d\n', u, v);
            end
        end
    end
    fclose(network_file_output_handle);

    clustering_file_output_path = strcat(output_prefix, 'com.tsv');
    clustering_file_output_handle = fopen(clustering_file_output_path, 'w');
    for i=1:N
        fprintf(clustering_file_output_handle, '%d\t%d\n', i, comm(i));
    end
    fclose(clustering_file_output_handle);
end