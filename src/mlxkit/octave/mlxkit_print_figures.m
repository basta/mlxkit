function mlxkit_print_figures(outdir, which)
  % Save open figures (all, or just the handles in `which`) as <outdir>/fig_<handle>.png.
  figs = findall(0, 'type', 'figure')';
  if nargin > 1
    figs = figs(ismember(figs, which));
  end
  for f = figs
    if isempty(findall(f, 'type', 'axes'))
      continue;  % nothing drawn yet (e.g. a bare `figure` call)
    end
    undo = {};
    try
      undo = latex_to_tex(f);
      set(f, 'paperpositionmode', 'auto');
      print(f, '-dpng', '-r96', fullfile(outdir, sprintf('fig_%d.png', f)));
    catch err
      fprintf(2, 'mlxkit: could not save figure %d: %s\n', f, err.message);
    end
    % Put the labels back, so the figure is unchanged for code that runs later.
    for i = 1:size(undo, 1)
      try
        set(undo{i, 1}, undo{i, 2}, undo{i, 3});
      catch
      end
    end
  end
end

function undo = latex_to_tex(fig)
  undo = cell(0, 3);
  % Octave's LaTeX interpreter needs a LaTeX install; MATLAB's doesn't. Rewrite
  % simple $math$ labels into Octave's TeX subset so they still render sensibly.
  for h = findall(fig, 'type', 'text')'
    if strcmp(get(h, 'interpreter'), 'latex')
      undo(end+1, :) = {h, 'string', get(h, 'string')};
      undo(end+1, :) = {h, 'interpreter', 'latex'};
      set(h, 'interpreter', 'tex', 'string', convert(get(h, 'string')));
    end
  end
  for ax = findall(fig, 'type', 'axes')'
    if isprop(ax, 'ticklabelinterpreter') && strcmp(get(ax, 'ticklabelinterpreter'), 'latex')
      undo(end+1, :) = {ax, 'ticklabelinterpreter', 'latex'};
      set(ax, 'ticklabelinterpreter', 'tex');
    end
  end
  for lg = findall(fig, 'type', 'legend')'
    if strcmp(get(lg, 'interpreter'), 'latex')
      undo(end+1, :) = {lg, 'string', get(lg, 'string')};
      undo(end+1, :) = {lg, 'interpreter', 'latex'};
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
