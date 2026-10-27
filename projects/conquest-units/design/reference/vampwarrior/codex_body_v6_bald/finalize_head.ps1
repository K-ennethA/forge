Add-Type -AssemblyName System.Drawing
$base = [Drawing.Bitmap]::new((Join-Path $PWD 'base2_back.png'))
$gen = [Drawing.Bitmap]::new('C:\Users\kenne\.codex\generated_images\01a17e4f-0831-7861-ab51-07edf8f45174\exec-dc6c626d-8e41-4952-b13a-2688a6e361b2.png')
$out = $base.Clone([Drawing.Rectangle]::new(0,0,1024,1536),[Drawing.Imaging.PixelFormat]::Format24bppRgb)
function IsSkin($c) { return ($c.R -gt 145 -and ($c.R-$c.G) -gt 9 -and $c.G -gt $c.B) }
$top=999; $minEar=999; $maxEar=0; $changed=0
for($y=45;$y -lt 220;$y++) {
 if($y -le 125){$sy=57+($y-54)*67/71} elseif($y -le 150){$sy=124+($y-125)*34/25} else {$sy=158+($y-150)*59/67}
 $sy=[Math]::Max(0,[Math]::Min(1535,[int][Math]::Round($sy)))
 for($x=440;$x -lt 582;$x++) {
  $sx=[int][Math]::Round(511+($x-511)/1.05)
  $c=$gen.GetPixel($sx,$sy); $b=$base.GetPixel($x,$y)
  $skin=IsSkin $c; $old=IsSkin $b; if(-not $old){ for($dy=-2;$dy -le 2;$dy++){for($dx=-2;$dx -le 2;$dx++){if(IsSkin ($base.GetPixel($x+$dx,$y+$dy))){$old=$true}}}}
  if(($skin -or $old) -and $y -ge 54) {
   if($y -ge 190 -and -not $old) {continue}
   if(-not $skin){ if($y -lt 188){$c=$base.GetPixel(430,$y)}else{$c=$gen.GetPixel($x,$y)} }; $out.SetPixel($x,$y,$c); $changed++
   if($skin){$top=[Math]::Min($top,$y); if($x -lt 458 -or $x -gt 565){$minEar=[Math]::Min($minEar,$y);$maxEar=[Math]::Max($maxEar,$y)}}
  }
 }
}
$out.Save((Join-Path $PWD 'edited2_back.png'),[Drawing.Imaging.ImageFormat]::Png)
Write-Output "size=1024x1536 top=$top ear_extent=$minEar..$maxEar changed_pixels=$changed"
$base.Dispose();$gen.Dispose();$out.Dispose()


