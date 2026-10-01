function p = currentProject()
  % mlxkit compat: the MATLAB project this script belongs to.
  root = mlxkit_project_root();
  [~, name] = fileparts(root);
  p = struct('RootFolder', root, 'Name', name);
end
