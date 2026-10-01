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
  write_file(fullfile(outdir, sprintf('%d.vars', k)), describe_displayed(txt));
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

function s = describe_displayed(txt)
  % JSON list of {name, class, size, matlab} for every variable the region
  % displayed (`name = ...`); `matlab` is MATLAB's display of the value.
  s = '[]';
  names = regexp(txt, '(?m)^(\w+) =', 'tokens');
  seen = {};
  info = {};
  for i = 1:numel(names)
    n = names{i}{1};
    if any(strcmp(seen, n)), continue; end
    seen{end+1} = n;
    try
      v = evalin('base', n);
    catch
      continue;
    end
    try
      shown = mlxkit_matlab_display(v);
    catch
      shown = '';
    end
    info{end+1} = struct('name', n, 'class', class(v), 'size', size(v), 'matlab', shown);
  end
  if ~isempty(info)
    s = jsonencode(info);
  end
end

function write_file(name, txt)
  fid = fopen(name, 'w');
  fwrite(fid, txt);
  fclose(fid);
end
