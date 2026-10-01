function parts = split(str, delim)
  % mlxkit compat: MATLAB split -> column cell array of char.
  if nargin < 2, delim = {' ', sprintf('\t'), sprintf('\n')}; end
  if isobject(str), str = char(str); end
  parts = strsplit(char(str), delim, 'CollapseDelimiters', false)';
end
