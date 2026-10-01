function ax = nexttile(varargin)
  % mlxkit compat: companion to the tiledlayout shim.
  global MLXKIT_TILES
  if isempty(MLXKIT_TILES) || ~ishandle(MLXKIT_TILES.fig)
    tiledlayout('flow');
  end
  T = MLXKIT_TILES;
  if ~isempty(varargin) && isnumeric(varargin{end}) && isscalar(varargin{end})
    T.k = varargin{end};
  else
    T.k = T.k + 1;
  end
  if T.flow
    n = ceil(sqrt(T.k)); m = ceil(T.k / n);
    T.m = m; T.n = n;
  end
  MLXKIT_TILES = T;
  figure(T.fig);
  ax = subplot(T.m, T.n, min(T.k, T.m * T.n));
end
