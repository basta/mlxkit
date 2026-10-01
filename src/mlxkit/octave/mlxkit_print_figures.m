function mlxkit_print_figures(outdir)
  % Save every open figure as <outdir>/fig_<handle>.png, the way it looks now.
  for f = findall(0, 'type', 'figure')'
    if isempty(findall(f, 'type', 'axes'))
      continue;  % nothing drawn yet (e.g. a bare `figure` call)
    end
    try
      latex_to_tex(f);
      set(f, 'paperpositionmode', 'auto');
      print(f, '-dpng', '-r96', fullfile(outdir, sprintf('fig_%d.png', f)));
    catch err
      fprintf(2, 'mlxkit: could not save figure %d: %s\n', f, err.message);
    end
  end
end

function latex_to_tex(fig)
  % Octave's LaTeX interpreter needs a LaTeX install; MATLAB's doesn't. Rewrite
  % simple $math$ labels into Octave's TeX subset so they still render sensibly.
  for h = findall(fig, 'type', 'text')'
    if strcmp(get(h, 'interpreter'), 'latex')
      set(h, 'interpreter', 'tex', 'string', convert(get(h, 'string')));
    end
  end
  for ax = findall(fig, 'type', 'axes')'
    if isprop(ax, 'ticklabelinterpreter') && strcmp(get(ax, 'ticklabelinterpreter'), 'latex')
      set(ax, 'ticklabelinterpreter', 'tex');
    end
  end
  for lg = findall(fig, 'type', 'legend')'
    if strcmp(get(lg, 'interpreter'), 'latex')
      set(lg, 'interpreter', 'tex', 'string', convert(get(lg, 'string')));
    end
  end
end

function s = convert(s)
  if iscell(s)
    s = cellfun(@convert, s, 'UniformOutput', false);
    return;
  end
  s = regexprep(s, '\\rm\s*', '\\rm ');
  s = regexprep(s, '\\mathrm\{([^}]*)\}', '{\\rm $1}');
  s = regexprep(s, '\\(left|right)', '');
  s = regexprep(s, '\\(,|;|!)', ' ');
  s = regexprep(s, '\$([^$]*)\$', '{\\it $1}');
  s = strrep(s, '$', '');
end
