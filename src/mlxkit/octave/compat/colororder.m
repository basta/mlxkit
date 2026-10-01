function c = colororder(varargin)
  % mlxkit compat: get/set the axes color order.
  args = varargin;
  target = gca();
  if ~isempty(args) && isscalar(args{1}) && ishghandle(args{1})
    target = args{1}; args(1) = [];
  end
  if strcmp(get(target, 'type'), 'figure'), target = get(target, 'currentaxes'); end
  if ~isempty(args)
    v = args{1};
    if ischar(v) || iscellstr(v) || isstring(v)
      names = cellstr(v);
      if numel(names) == 1
        palettes = struct('gem', [0 0.447 0.741; 0.85 0.325 0.098; 0.929 0.694 0.125; 0.494 0.184 0.556; 0.466 0.674 0.188; 0.301 0.745 0.933; 0.635 0.078 0.184]);
        if isfield(palettes, names{1}), v = palettes.(names{1}); else, v = get(target, 'colororder'); end
      else
        v = cell2mat(cellfun(@(n) mlxkit_color(n), names(:), 'UniformOutput', false));
      end
    end
    set(target, 'colororder', v);
  end
  c = get(target, 'colororder');
end

function rgb = mlxkit_color(name)
  names = {'r','g','b','c','m','y','k','w','red','green','blue','cyan','magenta','yellow','black','white'};
  vals = [1 0 0;0 1 0;0 0 1;0 1 1;1 0 1;1 1 0;0 0 0;1 1 1];
  vals = [vals; vals];
  i = find(strcmpi(names, name), 1);
  if isempty(i)
    rgb = sscanf(regexprep(name, '^#', ''), '%2x%2x%2x')' / 255;
  else
    rgb = vals(i, :);
  end
end
