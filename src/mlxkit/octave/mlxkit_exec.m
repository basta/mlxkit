function mlxkit_exec(outdir, regions)
  % Session version of mlxkit_run: run regions in the existing workspace,
  % save only the figures they touched, and describe the workspace afterwards.
  touched = [];
  for k = regions
    code = fileread(fullfile(outdir, sprintf('code_%d.m', k)));
    ok = mlxkit_region(k, code, outdir);
    figs = str2num(fileread(fullfile(outdir, sprintf('%d.figs', k))));
    touched = union(touched, figs(:)');
    if ~ok
      break;
    end
  end
  mlxkit_print_figures(outdir, touched);
  mlxkit_who(fullfile(outdir, 'workspace.tsv'));
end
