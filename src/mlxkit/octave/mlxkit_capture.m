function mlxkit_capture(outdir)
  % Run <outdir>/code.m in the base workspace; save what it printed to result.txt.
  code = fileread(fullfile(outdir, 'code.m'));
  try
    txt = evalc('evalin(''base'', code)');
  catch e
    txt = ['error: ' e.message];
  end
  fid = fopen(fullfile(outdir, 'result.txt'), 'w');
  fwrite(fid, txt);
  fclose(fid);
end
