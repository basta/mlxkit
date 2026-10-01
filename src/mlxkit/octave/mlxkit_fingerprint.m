function fp = mlxkit_fingerprint(fig)
  % A cheap summary of a figure's contents, used to notice which regions changed it.
  parts = {};
  for h = findall(fig)'
    t = get(h, 'type');
    s = t;
    switch t
      case {'line', 'surface', 'patch', 'scatter', 'image', 'hggroup'}
        for p = {'xdata', 'ydata', 'zdata', 'cdata', 'color', 'linestyle', 'marker'}
          if isprop(h, p{1})
            v = get(h, p{1});
            if isnumeric(v) || islogical(v)
              v = double(v(:));
              s = [s sprintf('|%d:%.10g', numel(v), sum(v(isfinite(v))))];
            else
              s = [s '|' char(v)];
            end
          end
        end
      case 'text'
        str = get(h, 'string');
        if iscell(str), str = strjoin(str, '\n'); end
        s = [s '|' char(str(:)')];
      case 'axes'
        s = [s sprintf('|%g', get(h, 'xlim'), get(h, 'ylim'), get(h, 'zlim'), get(h, 'position'))];
        s = [s '|' get(h, 'xgrid') get(h, 'ygrid') get(h, 'box') get(h, 'nextplot')];
        s = [s '|' get(h, 'xscale') get(h, 'yscale') sprintf('%g', get(h, 'xtick'), get(h, 'ytick'))];
        s = [s '|' sprintf('%g', isempty(get(h, 'xticklabel')))];
    end
    parts{end+1} = s;
  end
  fp = strjoin(parts, ';');
end
