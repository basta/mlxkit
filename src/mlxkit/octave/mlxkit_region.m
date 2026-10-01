function ok = mlxkit_region(k, code, outdir)
  % Run one live script region in the base workspace and record what it produced:
  %   <outdir>/<k>.txt      captured command window output
  %   <outdir>/<k>.err      error message, if the region failed
  %   <outdir>/<k>.figs     figure handles created or changed by the region
  persistent prints
  if isempty(prints)
    prints = containers.Map('KeyType', 'double', 'ValueType', 'any');
  end
  ok = true;
  err = '';
  try
    txt = evalc('evalin(''base'', code)');
  catch e
    txt = '';
    err = e.message;
    ok = false;
  end
  write_file(fullfile(outdir, sprintf('%d.txt', k)), txt);
  if ~isempty(err)
    write_file(fullfile(outdir, sprintf('%d.err', k)), err);
  end

  % A figure belongs to this region if it is new or its contents changed.
  touched = [];
  figs = findall(0, 'type', 'figure');
  for f = figs(:)'
    fp = mlxkit_fingerprint(f);
    if ~isKey(prints, f) || ~strcmp(prints(f), fp)
      touched(end+1) = f;
      prints(f) = fp;
    end
  end
  write_file(fullfile(outdir, sprintf('%d.figs', k)), sprintf('%d\n', touched));
end

function write_file(name, txt)
  fid = fopen(name, 'w');
  fwrite(fid, txt);
  fclose(fid);
end
