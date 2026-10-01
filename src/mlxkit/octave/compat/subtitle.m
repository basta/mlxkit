function h = subtitle(varargin)
  % mlxkit compat: add a second title line, as MATLAB's subtitle does.
  args = varargin;
  ax = gca();
  if ~isempty(args) && isscalar(args{1}) && ishghandle(args{1}) && strcmp(get(args{1}, 'type'), 'axes')
    ax = args{1}; args(1) = [];
  end
  t = get(ax, 'title');
  old = get(t, 'string');
  if iscell(old), old = old{1}; end
  set(t, 'string', {old, char(args{1})});
  h = t;
end
