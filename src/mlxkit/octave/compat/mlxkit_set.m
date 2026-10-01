function mlxkit_set(h, prop, value)
  % `h.Prop = value` for graphics handles. Properties Octave doesn't have
  % (e.g. YAxis.Exponent) are skipped, since they only affect appearance.
  if isempty(h), return; end
  try
    set(h, prop, value);
  catch err
    if isempty(regexp(err.message, 'unknown|invalid|not a valid|read-only', 'once'))
      rethrow(err);
    end
  end
end
