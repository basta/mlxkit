function s = mlxkit_matlab_display(v)
  % The value part of MATLAB's display of v (format short), or '' when we
  % leave it to Octave's own display (large or unusual values).
  s = '';
  if (isnumeric(v) || islogical(v)) && isscalar(v)
    s = fmt_scalar(v);
  elseif ischar(v) && (isrow(v) || isempty(v))
    s = ['''' v ''''];
  elseif isstruct(v) && isscalar(v)
    names = fieldnames(v);
    if isempty(names), return; end
    w = max(cellfun(@numel, names));
    lines = cell(numel(names), 1);
    for i = 1:numel(names)
      lines{i} = sprintf('    %*s: %s', w, names{i}, fmt_inline(v.(names{i})));
    end
    s = strjoin(lines, "\n");
  elseif iscell(v) && ndims(v) == 2 && numel(v) <= 50
    rows = cell(size(v, 1), 1);
    for r = 1:size(v, 1)
      items = cellfun(@(x) ['{' cell_item(x) '}'], v(r, :), 'UniformOutput', false);
      rows{r} = ['    ' strjoin(items, '    ')];
    end
    s = strjoin(rows, "\n");
  end
end

function s = fmt_scalar(x)
  if islogical(x)
    s = sprintf('%d', x);
  elseif isinteger(x)
    s = sprintf('%d', x);
  elseif ~isreal(x)
    s = sprintf('%s %s %si', fmt_scalar(real(x)), char('+' * (imag(x) >= 0) + '-' * (imag(x) < 0)), fmt_scalar(abs(imag(x))));
  elseif isnan(x)
    s = 'NaN';
  elseif isinf(x)
    s = ifelse_str(x > 0, 'Inf', '-Inf');
  elseif x == round(x) && abs(x) < 1e9
    s = sprintf('%d', x);
  elseif abs(x) >= 1e-3 && abs(x) < 1e5
    s = sprintf('%.4f', x);
  else
    s = sprintf('%.4e', x);
  end
end

function s = fmt_inline(x)
  % How MATLAB shows a value inside a struct or cell display.
  if ischar(x) && (isrow(x) || isempty(x))
    s = ['''' x ''''];
  elseif (isnumeric(x) || islogical(x)) && isscalar(x)
    s = fmt_scalar(x);
    if islogical(x), s = ifelse_str(x, 'true', 'false'); end
  elseif (isnumeric(x) || islogical(x)) && isrow(x) && numel(x) <= 10 && ~isempty(x)
    parts = arrayfun(@fmt_scalar, x, 'UniformOutput', false);
    s = ['[' strjoin(parts, ' ') ']'];
  elseif isempty(x) && isnumeric(x)
    s = '[]';
  else
    sz = strjoin(arrayfun(@num2str, size(x), 'UniformOutput', false), "\xC3\x97");
    s = ['[' sz ' ' class(x) ']'];
  end
end

function s = cell_item(x)
  % Inside cell displays MATLAB brackets numbers: {[4]}, {[1 2 3]}.
  s = fmt_inline(x);
  if (isnumeric(x) || islogical(x)) && isscalar(x)
    s = ['[' s ']'];
  end
end

function s = ifelse_str(c, a, b)
  if c, s = a; else, s = b; end
end
