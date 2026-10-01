function r = mlxkit_project_root(set_root)
  % Remembers the folder currentProject() reports as RootFolder.
  persistent root
  if nargin
    root = set_root;
  end
  r = root;
end
