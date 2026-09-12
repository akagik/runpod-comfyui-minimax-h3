// Generates a reproducible, metadata-only model index. Never downloads weights.
import {createHash} from 'node:crypto';
import {writeFile} from 'node:fs/promises';
const roots = [
  {repo:'Comfy-Org/MiniMax-H3', revision:'a98869194787969724c7425d95d0ed73ce9202af',
   select:p=>['diffusion_models/minimax_h3_fl2va_bf16.safetensors',
      'text_encoders/qwen3vl_32b_minimax_h3_bf16.safetensors',
      'vae/minimax_h3_video_vae_fp16.safetensors',
      'vae/minimax_h3_audio_vae_fp32.safetensors'].includes(p), map:p=>p},
  {repo:'OpenVDN/vdn-minimax-h3', revision:'51eeecefdb5b524c0df5539446d1dd54a17aa439',
   select:p=>p.startsWith('stage-dmd-step-250/') && !p.includes('/diffusers/'), map:p=>`vdn/${p}`},
];
const files=[];
for (const root of roots) {
  const r=await fetch(`https://huggingface.co/api/models/${root.repo}/revision/${root.revision}?blobs=true`);
  if(!r.ok) throw new Error(`Metadata HTTP ${r.status}`);
  const info=await r.json();
  if(info.sha!==root.revision) throw new Error('Revision mismatch');
  for(const f of info.siblings.filter(f=>root.select(f.rfilename))) {
    const url=`https://huggingface.co/${root.repo}/resolve/${root.revision}/${f.rfilename}`;
    let sha256=f.lfs?.sha256;
    if(!sha256) {
      if(f.size>1_000_000) throw new Error('Refusing large metadata download');
      const r=await fetch(url);
      if(!r.ok) throw new Error(`Small file HTTP ${r.status}`);
      const data=Buffer.from(await r.arrayBuffer());
      if(data.length!==f.size) throw new Error('Metadata length mismatch');
      sha256=createHash('sha256').update(data).digest('hex');
    }
    files.push({repo:root.repo, revision:root.revision, source:f.rfilename,
      path:root.map(f.rfilename), bytes:f.size, sha256, url});
  }
}
if(files.length!==12) throw new Error(`Expected 12 files, found ${files.length}`);
const manifest={schemaVersion:1, generatedAt:new Date().toISOString(),
  totalBytes:files.reduce((n,f)=>n+f.bytes,0), files};
await writeFile(new URL('../models.lock.json',import.meta.url),JSON.stringify(manifest,null,2)+'\n');
console.log(JSON.stringify({files:files.length,totalBytes:manifest.totalBytes}));
