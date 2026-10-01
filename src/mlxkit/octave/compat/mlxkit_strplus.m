function s = mlxkit_strplus(varargin)
  % MATLAB string addition: concatenate, converting numbers like string() does.
  s = '';
  for i = 1:nargin
    v = varargin{i};
    if ischar(v)
      s = [s v];
    elseif isobject(v) || iscellstr(v)
      s = [s char(v)];
    elseif islogical(v)
      if v, s = [s 'true']; else, s = [s 'false']; end
    elseif isnumeric(v) && isscalar(v)
      if v == round(v) && abs(v) < 1e15
        s = [s sprintf('%d', v)];
      else
        s = [s num2str(v)];
      end
    else
      s = [s mat2str(v)];
    end
  end
end
