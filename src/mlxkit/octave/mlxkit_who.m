function mlxkit_who(file)
  % Name, class and size of every variable in the base workspace.
  vars = evalin('base', 'whos');
  fid = fopen(file, 'w');
  for i = 1:numel(vars)
    v = vars(i);
    if strncmp(v.name, 'mlxkit_', 7), continue; end
    fprintf(fid, '%s\t%s\t%s\n', v.name, v.class, strjoin(arrayfun(@num2str, v.size, 'UniformOutput', false), 'x'));
  end
  fclose(fid);
end
