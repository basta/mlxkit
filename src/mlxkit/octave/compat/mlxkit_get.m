function v = mlxkit_get(h, prop)
  % Property lookup that yields [] for properties Octave doesn't have
  % (e.g. ax.YAxis), so assignments to their sub-properties can be skipped.
  try
    v = get(h, prop);
  catch
    v = [];
  end
end
