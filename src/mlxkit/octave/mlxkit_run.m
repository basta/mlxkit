function mlxkit_run(outdir, regions)
  % Run regions in order (code read from <outdir>/code_<k>.m), stopping at the
  % first error like the Live Editor does, then save every figure.
  for k = regions
    code = fileread(fullfile(outdir, sprintf('code_%d.m', k)));
    if ~mlxkit_region(k, code, outdir)
      break;
    end
  end
  mlxkit_print_figures(outdir);
end
