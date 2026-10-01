function varargout = tiledlayout(varargin)
  % mlxkit compat: minimal tiledlayout on top of subplot. Supports
  % tiledlayout(m, n), tiledlayout('flow') and name/value options (ignored).
  global MLXKIT_TILES
  m = 1; n = 1; flow = false;
  args = varargin;
  if ~isempty(args) && (isnumeric(args{1}) || isgraphics(args{1}))
    if isscalar(args{1}) && isgraphics(args{1}) && ~isnumeric(args{1})
      args(1) = [];
    end
  end
  if numel(args) >= 2 && isnumeric(args{1}) && isnumeric(args{2})
    m = args{1}; n = args{2};
  elseif ~isempty(args) && ischar(args{1}) && any(strcmpi(args{1}, {'flow', 'vertical', 'horizontal'}))
    flow = true;
  end
  f = gcf();
  clf(f);
  MLXKIT_TILES = struct('fig', f, 'm', m, 'n', n, 'k', 0, 'flow', flow);
  if nargout > 0
    varargout{1} = struct('Figure', f);
  end
end
