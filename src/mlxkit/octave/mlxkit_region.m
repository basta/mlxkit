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
  % class and size of every variable the region displayed (`name = ...`).
  s = '';
  names = regexp(txt, '(?m)^(\w+) =', 'tokens');
  seen = {};
  for i = 1:numel(names)
    n = names{i}{1};
    if any(strcmp(seen, n)), continue; end
    seen{end+1} = n;
    try
      v = evalin('base', n);
    catch
      continue;
    end
    sz = size(v);
    s = [s sprintf('%s\t%s\t%s\t%d\n', n, class(v), strjoin(arrayfun(@num2str, sz, 'UniformOutput', false), 'x'), isreal(v))];
  end
end

function write_file(name, txt)
  fid = fopen(name, 'w');
  fwrite(fid, txt);
  fclose(fid);
end
