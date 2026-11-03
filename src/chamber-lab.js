/* 입체 작품 전용. 운영 데이터·네트워크·저장소에 접근하지 않습니다. */
(async () => {
  const originalHtml = '<!doctype html>\n' + document.documentElement.outerHTML;
  const $ = (id) => document.getElementById(id);
  const icons = JSON.parse($('icons').textContent);
  const status = $('status');
  function fail(message) {
    status.replaceChildren(document.createTextNode(message), document.createElement('br'));
    const retry = document.createElement('button');
    retry.className = 'error-action'; retry.textContent = '다시 열기';
    retry.onclick = () => location.reload(); status.append(retry); status.hidden = false;
  }
  function notice(message) {
    $('notice').textContent = message;
    clearTimeout(notice.timer); notice.timer = setTimeout(() => { $('notice').textContent = ''; }, 2800);
  }
  let THREE;
  const urls = [];
  try {
    const engine = JSON.parse($('engine').textContent);
    const toModule = (source) => {
      const url = URL.createObjectURL(new Blob([source], {type:'text/javascript'})); urls.push(url); return url;
    };
    const decode = (value) => new TextDecoder().decode(Uint8Array.from(atob(value), (c) => c.charCodeAt(0)));
    const core = toModule(decode(engine.core));
    THREE = await import(toModule(decode(engine.renderer).replaceAll('"./three.core.min.js"', JSON.stringify(core))));
  } catch (error) { fail('작품을 불러오지 못했습니다. 브라우저의 스크립트 허용 상태를 확인해 주세요.'); return; }
  finally { urls.forEach((url) => URL.revokeObjectURL(url)); }

  const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
  let paused = reducedMotion.matches, hidden = document.hidden, lost = false;
  let renderer;
  try { renderer = new THREE.WebGLRenderer({canvas:$('scene'),antialias:true,alpha:false,powerPreference:'high-performance'}); }
  catch (error) { fail('WebGL을 사용할 수 없습니다. 브라우저의 그래픽 가속을 확인해 주세요.'); return; }
  renderer.setClearColor(0x080b0c, 1);
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.2;
  const scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0x080b0c, 0.014);
  const camera = new THREE.PerspectiveCamera(37, 1, 0.1, 100);
  const group = new THREE.Group(); scene.add(group);

  // 외부 텍스처 없이 반사광 환경을 절차적으로 생성합니다.
  const room = new THREE.Scene(); room.background = new THREE.Color(0x0b1412);
  function lightPanel(color, power, x, y, z, sx, sy, sz) {
    const panel = new THREE.Mesh(new THREE.BoxGeometry(sx,sy,sz), new THREE.MeshBasicMaterial({color:new THREE.Color(color).multiplyScalar(power)}));
    panel.position.set(x,y,z); room.add(panel);
  }
  lightPanel(0xe1f6ec,5,0,8,0,10,0.2,4);
  lightPanel(0x79d9c9,4,-7,2,1,0.2,8,4);
  lightPanel(0xf0b99c,4,8,0,1,0.2,8,3);
  lightPanel(0xffffff,6,0,2,9,1,8,0.2);
  const pmrem = new THREE.PMREMGenerator(renderer);
  const env = pmrem.fromScene(room, 0.05, 0.1, 80);
  scene.environment = env.texture; pmrem.dispose();
  room.traverse((item) => { item.geometry?.dispose(); item.material?.dispose(); });
  scene.add(new THREE.HemisphereLight(0xe4fff0,0x123d32,1.3));
  const key = new THREE.DirectionalLight(0xf4fffa,3); key.position.set(-4,6,8); scene.add(key);
  const rim = new THREE.DirectionalLight(0xe7a17c,2); rim.position.set(5,2,-3); scene.add(rim);

  const uniforms = {
    uTime:{value:0},uMix:{value:new THREE.Vector3(1,0,0)},
    uPointer:{value:new THREE.Vector3(50,50,50)},uHover:{value:0},
    uPulse:{value:-20},uPulsePoint:{value:new THREE.Vector3()},
    uDensity:{value:new THREE.Vector2(224,36)},
  };
  const surface = `
    uniform float uTime, uHover, uPulse;
    uniform vec2 uDensity;
    uniform vec3 uMix, uPointer, uPulsePoint;
    attribute vec3 aParam;
    varying float vEdge, vEnergy;
    varying vec3 vTint;
    const float TAU = 6.28318530718;
    vec3 knot(float u) {
      return vec3((2.0+0.65*cos(3.0*u))*cos(2.0*u),
                  (2.0+0.65*cos(3.0*u))*sin(2.0*u),1.25*sin(3.0*u));
    }
    vec3 field(float u, float v) {
      float wave=sin(u*7.0-v*2.0-uTime*0.65)*0.10;
      float tube=0.88+wave;
      vec3 a=vec3((2.35+tube*cos(v))*cos(u),tube*sin(v),(2.35+tube*cos(v))*sin(u));
      float tilt=0.95;
      a.yz=mat2(cos(tilt),-sin(tilt),sin(tilt),cos(tilt))*a.yz;
      vec3 k=knot(u);
      vec3 t=normalize(knot(u+0.002)-k);
      vec3 n=normalize(cross(t,vec3(0.0,0.0,1.0)));
      vec3 b=cross(t,n);
      vec3 c=k+(n*cos(v)+b*sin(v))*(0.46+wave);
      float twist=v+u*3.0+0.25*sin(uTime*0.2);
      vec3 d=vec3(cos(u)*(2.05+0.85*cos(twist)),1.1*sin(twist)+0.5*sin(u*3.0),sin(u)*(2.05+0.85*cos(twist)));
      d.yz=mat2(0.7,-0.714,0.714,0.7)*d.yz;
      return a*uMix.x+c*uMix.y+d*uMix.z;
    }
    void tile(out vec3 p,out mat3 basis,out float energy) {
      float u=aParam.x*TAU, v=aParam.y*TAU;
      p=field(u,v);
      vec3 tu=normalize(field(u+0.003,v)-p);
      vec3 tv=normalize(field(u,v+0.003)-p);
      vec3 n=normalize(cross(tu,tv)); tv=normalize(cross(n,tu));
      float near=exp(-dot(p-uPointer,p-uPointer)*1.1)*uHover;
      float elapsed=uTime-uPulse;
      float wave=sin(length(p-uPulsePoint)*4.0-elapsed*4.5)*exp(-abs(length(p-uPulsePoint)-elapsed*1.5)*2.0)*exp(-elapsed*0.45);
      wave*=step(0.0,elapsed);
      float breathing=0.07*sin(u*5.0+v*2.0-uTime*0.7);
      p+=n*(breathing+near*0.8+wave*0.5);
      float turn=near*0.9+wave*0.35+0.10*sin(u*6.0+uTime*0.35);
      vec3 tilt=tv*cos(turn)+n*sin(turn);
      basis=mat3(tu,tilt,normalize(cross(tu,tilt)));
      energy=near+abs(wave)*0.5;
    }
  `;
  const material = new THREE.MeshPhysicalMaterial({color:0xd5e1d8,metalness:0.78,roughness:0.22,clearcoat:0.9,clearcoatRoughness:0.15,iridescence:0.3,iridescenceIOR:1.3,iridescenceThicknessRange:[100,180],envMapIntensity:2,side:THREE.DoubleSide});
  material.onBeforeCompile = (shader) => {
    Object.assign(shader.uniforms,uniforms);
    shader.vertexShader = surface + shader.vertexShader;
    shader.vertexShader = shader.vertexShader.replace('#include <beginnormal_vertex>', `
      vec3 tilePosition; mat3 tileBasis; float energy;
      tile(tilePosition,tileBasis,energy);
      vec3 objectNormal=tileBasis*normal;
      vEdge=abs(position.z)*2.0; vEnergy=energy;
      float band=0.5+0.5*sin(aParam.x*TAU*2.0+aParam.y*TAU+uTime*0.09);
      vTint=mix(vec3(0.20,0.64,0.56),vec3(0.88,0.92,0.85),smoothstep(0.08,0.7,band));
      vTint=mix(vTint,vec3(0.83,0.46,0.25),pow(max(0.0,sin(aParam.x*TAU-0.7)),18.0)*0.7);
    `);
    shader.vertexShader = shader.vertexShader.replace('#include <begin_vertex>', `
      float sx=(0.075+uMix.y*0.055)*224.0/uDensity.x;
      float sy=(0.165-uMix.y*0.07)*28.0/uDensity.y;
      vec3 transformed=tilePosition+tileBasis*(position*vec3(sx,sy,0.032));
    `);
    shader.fragmentShader = 'varying float vEdge,vEnergy; varying vec3 vTint;\n' + shader.fragmentShader;
    shader.fragmentShader = shader.fragmentShader.replace('#include <color_fragment>', '#include <color_fragment>\ndiffuseColor.rgb*=vTint;');
    shader.fragmentShader = shader.fragmentShader.replace('#include <emissivemap_fragment>', '#include <emissivemap_fragment>\ntotalEmissiveRadiance+=vec3(0.12,0.62,0.45)*vEnergy*0.4;');
  };
  let geometry;
  function makeGeometry(countU,countV){
    const result=new THREE.InstancedBufferGeometry().copy(new THREE.BoxGeometry(1,1,1));
    const count=countU*countV,params=new Float32Array(count*3);
    for(let i=0;i<count;i++){params[i*3]=Math.floor(i/countV)/countU;params[i*3+1]=(i%countV)/countV;params[i*3+2]=(i*0.6180339)%1;}
    result.setAttribute('aParam',new THREE.InstancedBufferAttribute(params,3));result.instanceCount=count;
    return result;
  }
  geometry=makeGeometry(224,36);
  const sculpture=new THREE.Mesh(geometry,material); sculpture.frustumCulled=false; group.add(sculpture);

  // 반사상도 같은 변위식을 사용하므로 본체의 움직임과 함께 변화합니다.
  const reflectionMaterial=material.clone(); reflectionMaterial.onBeforeCompile=material.onBeforeCompile;
  reflectionMaterial.transparent=true;reflectionMaterial.opacity=0.14;reflectionMaterial.depthWrite=false;
  const reflection=new THREE.Mesh(geometry,reflectionMaterial);reflection.frustumCulled=false;
  const mirrorGroup=new THREE.Group();mirrorGroup.scale.y=-1;mirrorGroup.position.y=-7.5;mirrorGroup.add(reflection);scene.add(mirrorGroup);
  const floorMaterial=new THREE.ShaderMaterial({transparent:true,depthWrite:false,uniforms:{uTime:uniforms.uTime},vertexShader:`
    varying vec3 vP;void main(){vP=position;gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0);}
  `,fragmentShader:`
    varying vec3 vP;uniform float uTime;
    void main(){float radius=length(vP.xy);float fade=exp(-radius*0.14);
      float rings=pow(0.5+0.5*cos(radius*4.0-uTime*0.45),14.0)*0.12;
      float grid=pow(0.5+0.5*cos(vP.x*1.1),180.0)+pow(0.5+0.5*cos(vP.y*1.1),180.0);
      gl_FragColor=vec4(vec3(0.22,0.36,0.30),(rings+grid*0.055)*fade);}
  `});
  const floor=new THREE.Mesh(new THREE.PlaneGeometry(70,70),floorMaterial);floor.rotation.x=-Math.PI/2;floor.position.y=-3.75;scene.add(floor);

  // 가는 빛의 선도 본체와 같은 곡면을 따라 움직입니다.
  const threadGeo=new THREE.BufferGeometry(),threadParams=[];
  for(let ring=0;ring<4;ring++)for(let j=0;j<448;j++)for(let end=0;end<2;end++)threadParams.push((j+end)/448,ring/4,0);
  threadGeo.setAttribute('position',new THREE.Float32BufferAttribute(new Array(threadParams.length).fill(0),3));
  threadGeo.setAttribute('aParam',new THREE.Float32BufferAttribute(threadParams,3));
  const threadMat=new THREE.ShaderMaterial({transparent:true,depthWrite:false,blending:THREE.AdditiveBlending,uniforms,
    vertexShader:surface+`void main(){vec3 p;mat3 basis;float e;tile(p,basis,e);vEnergy=e;vEdge=aParam.x;gl_Position=projectionMatrix*modelViewMatrix*vec4(p+basis[2]*0.06,1.0);}`,
    fragmentShader:`varying float vEnergy,vEdge;void main(){gl_FragColor=vec4(mix(vec3(0.32,0.65,0.54),vec3(0.85,0.72,0.48),vEdge),0.35+vEnergy*0.4);}`});
  const threads=new THREE.LineSegments(threadGeo,threadMat);threads.frustumCulled=false;group.add(threads);

  let yaw=0,targetYaw=0,pitch=0,targetPitch=0,distance=12.6,targetDistance=12.6;
  let elapsed=0,last=performance.now(),raf=0,frameCount=0,slowFrames=0,autoQuality=1.5;
  let targetForm=0,autoElapsed=0,transition=1;const fromMix=new THREE.Vector3(1,0,0),toMix=new THREE.Vector3(1,0,0);
  let pointerActive=false,drag=null;const pointer=new THREE.Vector2(),ray=new THREE.Raycaster();
  const plane=new THREE.Plane(new THREE.Vector3(0,0,1),0),worldHit=new THREE.Vector3();
  const chapters=[['I / CONTINUUM','The space between.','연속과 틈 사이'],['II / ENTANGLEMENT','Nothing is separate.','서로를 통과하는 순간'],['III / RESONANCE','A different kind of quiet.','고요의 또 다른 형태']];
  function chooseForm(index,manual=true){
    fromMix.copy(uniforms.uMix.value);toMix.set(+(index===0),+(index===1),+(index===2));
    targetForm=index;transition=0;autoElapsed=0;
    if(manual)$('auto').checked=false;
    document.querySelectorAll('[data-form]').forEach((b)=>b.setAttribute('aria-pressed',String(Number(b.dataset.form)===index)));
    $('chapter').textContent=chapters[index][0];$('name').textContent=chapters[index][1];$('subtitle').textContent=chapters[index][2];
    if(paused){uniforms.uMix.value.copy(toMix);transition=1;requestFrame();}
  }
  document.querySelectorAll('[data-form]').forEach((b)=>{b.onclick=()=>chooseForm(Number(b.dataset.form));});
  function resize(){
    const w=innerWidth,h=innerHeight;
    const choice=$('quality').value;
    const cap=choice==='eco'?0.9:choice==='high'?2:autoQuality;
    reflectionMaterial.opacity=w<620?0.065:0.14;
    const density=choice==='high'?[256,40]:choice==='eco'||w<620||autoQuality<=1?[128,24]:[224,36];
    if(uniforms.uDensity.value.x!==density[0]||uniforms.uDensity.value.y!==density[1]){
      geometry.dispose();geometry=makeGeometry(...density);sculpture.geometry=geometry;reflection.geometry=geometry;uniforms.uDensity.value.set(...density);
    }
    renderer.setPixelRatio(Math.min(devicePixelRatio||1,cap));renderer.setSize(w,h,false);
    camera.aspect=w/h;camera.updateProjectionMatrix();requestFrame();
  }
  $('quality').onchange=resize;addEventListener('resize',resize);
  function syncPause(){
    $('pause').innerHTML=paused?icons.play:icons.pause;
    $('pause').setAttribute('aria-pressed',String(paused));
    $('pause').title=$('pause').ariaLabel=paused?'재생':'일시정지';
    if(audio)audio.gain.gain.setTargetAtTime(soundOn&&!paused&&!hidden?0.075:0,audio.ctx.currentTime,0.3);
  }
  let audio=null,soundOn=false;
  function setPaused(value){paused=value;syncPause();last=performance.now();requestFrame();}
  $('pause').onclick=()=>setPaused(!paused);
  $('reset').onclick=()=>{targetYaw=0;targetPitch=0;targetDistance=12.6;requestFrame();};
  reducedMotion.addEventListener('change',(event)=>{if(event.matches){$('auto').checked=false;setPaused(true);}});
  if(reducedMotion.matches)$('auto').checked=false;
  $('fullscreen').onclick=async()=>{try{if(document.fullscreenElement)await document.exitFullscreen();else await document.documentElement.requestFullscreen();}catch(error){notice('이 브라우저에서는 전체 화면을 사용할 수 없습니다.');}};
  if(!document.documentElement.requestFullscreen)$('fullscreen').disabled=true;
  $('download').onclick=()=>{
    const url=URL.createObjectURL(new Blob([originalHtml],{type:'text/html;charset=utf-8'}));
    const a=document.createElement('a');a.href=url;a.download='Field-Lab-07.html';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  };
  $('sound').onclick=async()=>{
    try{
      if(!audio){
        const ctx=new(window.AudioContext||window.webkitAudioContext)(),gain=ctx.createGain();gain.gain.value=0;gain.connect(ctx.destination);
        const oscillators=[55,82.4069,110,164.8138].map((frequency,i)=>{const oscillator=ctx.createOscillator(),level=ctx.createGain();oscillator.type='sine';oscillator.frequency.value=frequency;level.gain.value=0.22/(1+i*0.45);oscillator.connect(level);level.connect(gain);oscillator.start();return oscillator;});
        audio={ctx,gain,oscillators};
      }
      soundOn=!soundOn;if(soundOn)await audio.ctx.resume();
      $('sound').innerHTML=soundOn?icons.volume2:icons.volumex;$('sound').setAttribute('aria-pressed',String(soundOn));
      $('sound').title=$('sound').ariaLabel=soundOn?'소리 끄기':'소리 켜기';syncPause();
    }catch(error){notice('소리를 시작하지 못했습니다.');}
  };
  function pointerPosition(event){pointer.set(event.clientX/innerWidth*2-1,1-event.clientY/innerHeight*2);pointerActive=true;requestFrame();}
  $('scene').addEventListener('pointermove',(event)=>{
    pointerPosition(event);
    if(drag&&event.pointerId===drag.id){const dx=event.clientX-drag.x,dy=event.clientY-drag.y;targetYaw+=dx*0.007;targetPitch=Math.max(-0.7,Math.min(0.7,targetPitch+dy*0.005));drag.distance+=Math.abs(dx)+Math.abs(dy);drag.x=event.clientX;drag.y=event.clientY;}
  });
  $('scene').addEventListener('pointerdown',(event)=>{pointerPosition(event);drag={id:event.pointerId,x:event.clientX,y:event.clientY,distance:0};$('scene').setPointerCapture(event.pointerId);});
  function release(event){if(drag&&drag.id===event.pointerId){if(drag.distance<8){uniforms.uPulse.value=elapsed;uniforms.uPulsePoint.value.copy(uniforms.uPointer.value);}drag=null;if(event.pointerType==='touch')pointerActive=false;requestFrame();}}
  $('scene').addEventListener('pointerup',release);$('scene').addEventListener('pointercancel',()=>{drag=null;pointerActive=false;});
  $('scene').addEventListener('pointerleave',()=>{if(!drag)pointerActive=false;});
  $('scene').addEventListener('wheel',(event)=>{event.preventDefault();targetDistance=Math.max(9,Math.min(20,targetDistance+event.deltaY*0.008));requestFrame();},{passive:false});
  addEventListener('keydown',(event)=>{if(event.target.closest('input,select,button,a'))return;if(event.code==='Space'){event.preventDefault();setPaused(!paused);}if(['1','2','3'].includes(event.key))chooseForm(Number(event.key)-1);});
  document.addEventListener('visibilitychange',()=>{hidden=document.hidden;if(hidden)cancelAnimationFrame(raf);raf=0;syncPause();if(!hidden){last=performance.now();requestFrame();}});
  $('scene').addEventListener('webglcontextlost',(event)=>{event.preventDefault();lost=true;cancelAnimationFrame(raf);raf=0;fail('그래픽 연결이 중단되었습니다. 다시 열어 주세요.');});
  function requestFrame(){if(!raf&&!hidden&&!lost)raf=requestAnimationFrame(frame);}
  function frame(now){
    raf=0;if(hidden||lost)return;
    const rawDt=(now-last)/1000,dt=Math.min(0.05,Math.max(0,rawDt));last=now;
    if(!paused){elapsed+=dt*Number($('tempo').value);autoElapsed+=dt;transition=Math.min(1,transition+dt/3.8);}
    const ease=transition*transition*(3-2*transition);uniforms.uMix.value.lerpVectors(fromMix,toMix,ease);
    if(!paused&&$('auto').checked&&autoElapsed>16)chooseForm((targetForm+1)%3,false);
    uniforms.uTime.value=elapsed;
    yaw+=(targetYaw-yaw)*(paused?1:0.10);pitch+=(targetPitch-pitch)*(paused?1:0.10);distance+=(targetDistance-distance)*(paused?1:0.08);
    group.rotation.set(pitch, yaw+elapsed*0.055, Math.sin(elapsed*0.12)*0.035);
    reflection.rotation.copy(group.rotation);
    const fit=Math.max(1,0.95/camera.aspect);
    camera.position.set(0,1.1,distance*fit);camera.lookAt(0,-0.2,0);camera.updateMatrixWorld();group.updateMatrixWorld();
    if(pointerActive){ray.setFromCamera(pointer,camera);if(ray.ray.intersectPlane(plane,worldHit)){group.worldToLocal(worldHit);uniforms.uPointer.value.lerp(worldHit,0.16);}}
    uniforms.uHover.value+=((pointerActive?1:0)-uniforms.uHover.value)*0.1;
    renderer.render(scene,camera);frameCount++;
    if(frameCount===1){status.hidden=true;document.body.dataset.ready='true';}
    $('scene').dataset.frame=String(frameCount);$('scene').dataset.form=String(targetForm);
    if(frameCount>40&&rawDt>0.045&&rawDt<0.5)slowFrames++;else slowFrames=Math.max(0,slowFrames-1);
    if(slowFrames>45&&autoQuality>0.85&&$('quality').value==='auto'){autoQuality=Math.max(0.85,autoQuality-0.25);slowFrames=0;resize();}
    if(!paused)requestFrame();
  }
  addEventListener('pagehide',(event)=>{
    cancelAnimationFrame(raf);raf=0;
    if(event.persisted){hidden=true;syncPause();return;}
    audio?.ctx.close();renderer.dispose();env.dispose();geometry.dispose();material.dispose();reflectionMaterial.dispose();threadGeo.dispose();threadMat.dispose();floor.geometry.dispose();floorMaterial.dispose();
  });
  addEventListener('pageshow',(event)=>{if(event.persisted){hidden=false;last=performance.now();syncPause();requestFrame();}});
  syncPause();resize();requestFrame();
})();
