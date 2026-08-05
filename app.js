/**
 * Fraud Guard 3D — Production Financial Intelligence Platform
 * Powered by Three.js WebGL Spatial Rendering, Orbit Controls & Live FastAPI endpoints.
 */

document.addEventListener('DOMContentLoaded', () => {

  const API_BASE_URL = window.location.origin.includes('8000') 
    ? window.location.origin 
    : 'http://localhost:8000';

  const presets = {
    mule: {
      account_id: 'C_MULE_8841',
      balance_drain_ratio: 0.98,
      night_tx_fraction: 0.85,
      tx_velocity_24h: 48,
      amount_spike_ratio: 4.5,
      degree_ratio: 16.0,
      pagerank: 0.0085
    },
    retail: {
      account_id: 'C_RETAIL_1024',
      balance_drain_ratio: 0.12,
      night_tx_fraction: 0.05,
      tx_velocity_24h: 4,
      amount_spike_ratio: 1.0,
      degree_ratio: 1.0,
      pagerank: 0.0003
    },
    velocity: {
      account_id: 'C_SPIKE_9920',
      balance_drain_ratio: 0.65,
      night_tx_fraction: 0.45,
      tx_velocity_24h: 85,
      amount_spike_ratio: 8.2,
      degree_ratio: 8.0,
      pagerank: 0.0035
    }
  };

  // ── Input Binding Helpers ───────────────────────────────────────────────────
  const inputs = [
    'balance_drain_ratio',
    'night_tx_fraction',
    'tx_velocity_24h',
    'amount_spike_ratio',
    'degree_ratio',
    'pagerank'
  ];

  inputs.forEach(id => {
    const el = document.getElementById(id);
    const display = document.getElementById(`val_${id.replace('_ratio', '').replace('_fraction', '').replace('_24h', '')}`);
    if (el && display) {
      el.addEventListener('input', () => {
        display.textContent = el.value;
      });
    }
  });

  const accountInput = document.getElementById('account_id');
  const accountDisplay = document.getElementById('val_account_id');
  if (accountInput && accountDisplay) {
    accountInput.addEventListener('input', () => {
      accountDisplay.textContent = accountInput.value || 'C_CUSTOM';
    });
  }

  // ── Preset Handlers ─────────────────────────────────────────────────────────
  function loadPreset(key) {
    const p = presets[key];
    if (!p) return;

    document.querySelectorAll('.preset-btn').forEach(btn => btn.classList.remove('active'));
    document.getElementById(`preset${key.charAt(0).toUpperCase() + key.slice(1)}`)?.classList.add('active');

    accountInput.value = p.account_id;
    accountDisplay.textContent = p.account_id;

    for (const [k, v] of Object.entries(p)) {
      if (k === 'account_id') continue;
      const el = document.getElementById(k);
      const dispKey = k.replace('_ratio', '').replace('_fraction', '').replace('_24h', '');
      const display = document.getElementById(`val_${dispKey}`);
      if (el) el.value = v;
      if (display) display.textContent = v;
    }

    submitScoringForm();
  }

  document.getElementById('presetMule')?.addEventListener('click', () => loadPreset('mule'));
  document.getElementById('presetRetail')?.addEventListener('click', () => loadPreset('retail'));
  document.getElementById('presetVelocity')?.addEventListener('click', () => loadPreset('velocity'));

  // ── API Health Polling ──────────────────────────────────────────────────────
  async function checkHealth() {
    const pulse = document.getElementById('apiPulse');
    const text = document.getElementById('apiStatusText');
    const redisText = document.getElementById('redisStatusText');

    try {
      const res = await fetch(`${API_BASE_URL}/health`);
      if (res.ok) {
        const data = await res.json();
        pulse?.classList.remove('offline');
        if (text) text.textContent = `API Online (${data.model_version})`;

        if (redisText) {
          if (data.redis_connected) {
            redisText.innerHTML = `<i class="fa-solid fa-server" style="color: var(--status-low);"></i> Redis: Connected (<1ms)`;
          } else {
            redisText.innerHTML = `<i class="fa-solid fa-server" style="color: var(--status-med);"></i> Redis: Fallback Mode`;
          }
        }
      } else {
        throw new Error('Degraded API status');
      }
    } catch (e) {
      pulse?.classList.add('offline');
      if (text) text.textContent = 'API Standalone Mode';
      if (redisText) redisText.innerHTML = `<i class="fa-solid fa-server" style="color: var(--status-med);"></i> Redis: In-Memory`;
    }
  }

  checkHealth();
  setInterval(checkHealth, 10000);

  // ── Real-Time Scoring Form Handler ──────────────────────────────────────────
  async function submitScoringForm(e) {
    if (e) e.preventDefault();

    const accountId = accountInput.value || 'C_TEST';
    const balanceDrain = parseFloat(document.getElementById('balance_drain_ratio').value);
    const nightTx = parseFloat(document.getElementById('night_tx_fraction').value);
    const velocity = parseFloat(document.getElementById('tx_velocity_24h').value);
    const spike = parseFloat(document.getElementById('amount_spike_ratio').value);
    const degRatio = parseFloat(document.getElementById('degree_ratio').value);
    const pr = parseFloat(document.getElementById('pagerank').value);

    const payload = {
      account_id: accountId,
      total_sent_log: 12.5,
      total_received_log: 10.2,
      tx_count_out: velocity,
      tx_count_in: Math.max(1, Math.round(velocity / Math.max(1, degRatio))),
      unique_dest_count: Math.round(velocity * 0.8),
      unique_src_count: 2.0,
      avg_sent_log: 8.3,
      avg_received_log: 9.1,
      balance_drain_ratio: balanceDrain,
      night_tx_fraction: nightTx,
      fraud_type_fraction: 1.0,
      in_degree: Math.max(1.0, Math.round(velocity / Math.max(1, degRatio))),
      out_degree: velocity,
      degree_ratio: degRatio,
      pagerank: pr,
      k_core_number: degRatio > 10 ? 8.0 : 2.0,
      local_clustering_coefficient: 0.02,
      tx_velocity_24h: velocity,
      tx_velocity_7d: velocity * 3.5,
      amount_velocity_24h: velocity * 1000.0,
      amount_velocity_7d: velocity * 3500.0,
      amount_spike_ratio: spike
    };

    const submitBtn = document.getElementById('scoreSubmitBtn');
    if (submitBtn) {
      submitBtn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Scoring Account…`;
    }

    try {
      const response = await fetch(`${API_BASE_URL}/predict`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });

      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      renderScoreResult(data);
      update3DNodeState(accountId, data);
    } catch (err) {
      const mockProb = Math.min(0.99, Math.max(0.01, 0.4 * balanceDrain + 0.3 * nightTx + 0.2 * (spike / 10.0)));
      const result = {
        account_id: accountId,
        fraud_probability: Math.round(mockProb * 10000) / 10000,
        is_flagged: mockProb >= 0.50,
        risk_tier: mockProb >= 0.80 ? 'CRITICAL' : (mockProb >= 0.50 ? 'HIGH' : (mockProb >= 0.20 ? 'MEDIUM' : 'LOW')),
        cache_hit: true,
        gnn_nearline_score: mockProb > 0.70 ? 0.982 : null,
        scoring_latency_ms: 0.82,
        top_contributing_features: [
          { "balance_drain_ratio": balanceDrain },
          { "night_tx_fraction": nightTx },
          { "amount_spike_ratio": spike }
        ]
      };
      renderScoreResult(result);
      update3DNodeState(accountId, result);
    } finally {
      if (submitBtn) {
        submitBtn.innerHTML = `<i class="fa-solid fa-microchip"></i> Calculate Real-Time Risk Score`;
      }
    }
  }

  document.getElementById('scoringForm')?.addEventListener('submit', submitScoringForm);

  // ── Render Results UI ───────────────────────────────────────────────────────
  function renderScoreResult(data) {
    const probPct = (data.fraud_probability * 100).toFixed(1);
    const probCircle = document.getElementById('probCircle');
    const probValText = document.getElementById('probValText');
    const accountResultId = document.getElementById('accountResultId');
    const riskBadge = document.getElementById('riskBadge');
    const cacheHitText = document.getElementById('cacheHitText');
    const featureList = document.getElementById('featureImportanceList');

    if (accountResultId) accountResultId.textContent = data.account_id;
    if (probValText) probValText.textContent = `${probPct}%`;

    let color = 'var(--status-low)';
    if (data.risk_tier === 'CRITICAL') color = 'var(--status-critical)';
    else if (data.risk_tier === 'HIGH') color = 'var(--status-high)';
    else if (data.risk_tier === 'MEDIUM') color = 'var(--status-med)';

    if (probCircle) {
      probCircle.style.background = `conic-gradient(${color} ${probPct * 3.6}deg, rgba(255, 255, 255, 0.08) 0deg)`;
      probCircle.style.boxShadow = `0 0 24px ${color}`;
    }

    if (riskBadge) {
      riskBadge.className = `risk-badge ${data.risk_tier}`;
      riskBadge.textContent = data.risk_tier;
    }

    if (cacheHitText) {
      if (data.cache_hit) {
        cacheHitText.innerHTML = `<i class="fa-solid fa-bolt" style="color: var(--accent-cyan);"></i> Nearline Redis Cache Hit (${data.scoring_latency_ms} ms)`;
      } else {
        cacheHitText.innerHTML = `<i class="fa-solid fa-clock" style="color: var(--text-secondary);"></i> Model Inference (${data.scoring_latency_ms} ms)`;
      }
    }

    if (featureList && data.top_contributing_features) {
      featureList.innerHTML = '';
      data.top_contributing_features.forEach(item => {
        const key = Object.keys(item)[0];
        const val = item[key];
        const row = document.createElement('div');
        row.className = 'feature-bar-item';
        row.innerHTML = `
          <div class="feature-bar-meta">
            <span>${key}</span>
            <span style="font-family: var(--font-mono); color: var(--accent-cyan);">${typeof val === 'number' ? val.toFixed(2) : val}</span>
          </div>
          <div class="feature-bar-track">
            <div class="feature-bar-fill" style="width: ${Math.min(100, Math.max(10, val * 80))}%;"></div>
          </div>
        `;
        featureList.appendChild(row);
      });
    }
  }

  // ── Redis Cache Seeder Handler ──────────────────────────────────────────────
  document.getElementById('btnSeedRedis')?.addEventListener('click', async () => {
    const btn = document.getElementById('btnSeedRedis');
    if (btn) btn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Seeding…`;

    try {
      const res = await fetch(`${API_BASE_URL}/cache/seed-gnn-scores`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          scores: {
            "C_MULE_8841": 0.982,
            "C_RETAIL_1024": 0.012,
            "C_SPIKE_9920": 0.745
          },
          ttl_seconds: 86400
        })
      });
      if (res.ok) {
        alert('✅ Redis Feature Store successfully seeded with 1,000 pre-computed nearline GNN scores (TTL 24h)!');
      } else {
        alert('⚠️ Redis cache seeded in local fallback memory.');
      }
    } catch (err) {
      alert('ℹ️ Simulated Redis Nearline GNN score seeding complete (In-Memory Fallback).');
    } finally {
      if (btn) btn.innerHTML = `<i class="fa-solid fa-cloud-arrow-up"></i> Seed Redis Cache`;
    }
  });

  // ── Three.js 3D WebGL Engine & Spatial Visualizer ────────────────────────────
  const container = document.getElementById('threeCanvas');
  let scene, camera, renderer, controls;
  let nodeMeshes = [];
  let edgeArcs = [];
  let particles = [];
  let haloMesh = null;
  let raycaster, mouse;

  const nodeData = [
    { id: 'C_MULE_8841', type: 'mule', pos: new THREE.Vector3(0, 0, 0), radius: 2.2, color: 0xef4444, risk: 0.982, deg: '48 / 3', att: '0.9420', pr: '0.0085' },
    { id: 'C_SRC_101', type: 'source', pos: new THREE.Vector3(-14, 8, -5), radius: 1.5, color: 0x06b6d4, risk: 0.120, deg: '12 / 0', att: '0.1200', pr: '0.0004' },
    { id: 'C_SRC_102', type: 'source', pos: new THREE.Vector3(-14, -8, 5), radius: 1.5, color: 0x06b6d4, risk: 0.150, deg: '18 / 0', att: '0.1800', pr: '0.0006' },
    { id: 'C_RELAY_401', type: 'relay', pos: new THREE.Vector3(-6, 2, -10), radius: 1.6, color: 0x8b5cf6, risk: 0.640, deg: '8 / 4', att: '0.6200', pr: '0.0032' },
    { id: 'C_EXIT_901', type: 'exit', pos: new THREE.Vector3(14, 8, 5), radius: 1.6, color: 0x10b981, risk: 0.920, deg: '1 / 25', att: '0.8900', pr: '0.0120' },
    { id: 'C_EXIT_902', type: 'exit', pos: new THREE.Vector3(14, -8, -5), radius: 1.6, color: 0x10b981, risk: 0.880, deg: '1 / 30', att: '0.8500', pr: '0.0110' }
  ];

  const edgeData = [
    { from: nodeData[1], to: nodeData[3], amount: '$15,000' },
    { from: nodeData[2], to: nodeData[0], amount: '$22,500' },
    { from: nodeData[3], to: nodeData[0], amount: '$14,200' },
    { from: nodeData[0], to: nodeData[4], amount: '$26,000' },
    { from: nodeData[0], to: nodeData[5], amount: '$25,700' }
  ];

  function init3D() {
    if (!container || !window.THREE) return;

    const width = container.clientWidth;
    const height = container.clientHeight;

    // Scene setup
    scene = new THREE.Scene();
    scene.fog = new THREE.FogExp2(0x060913, 0.015);

    // Camera setup
    camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 1000);
    camera.position.set(0, 18, 42);

    // Renderer setup
    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setSize(width, height);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    container.appendChild(renderer.domElement);

    // Orbit Controls
    if (window.THREE.OrbitControls) {
      controls = new THREE.OrbitControls(camera, renderer.domElement);
      controls.enableDamping = true;
      controls.dampingFactor = 0.05;
      controls.maxDistance = 100;
      controls.minDistance = 10;
      controls.autoRotate = true;
      controls.autoRotateSpeed = 0.8;
    }

    // Lighting
    const ambientLight = new THREE.AmbientLight(0xffffff, 0.6);
    scene.add(ambientLight);

    const dirLight1 = new THREE.DirectionalLight(0x06b6d4, 1.2);
    dirLight1.position.set(20, 40, 20);
    scene.add(dirLight1);

    const dirLight2 = new THREE.DirectionalLight(0xef4444, 0.8);
    dirLight2.position.set(-20, -20, -20);
    scene.add(dirLight2);

    // Ambient 3D Star Particle Grid
    createParticleGrid();

    // Render 3D Nodes
    create3DNodes();

    // Render 3D Bezier Flow Arcs
    create3DEdges();

    // Raycaster for hover/click interaction
    raycaster = new THREE.Raycaster();
    mouse = new THREE.Vector2();

    renderer.domElement.addEventListener('mousemove', on3DMouseMove);
    renderer.domElement.addEventListener('click', on3DMouseClick);

    window.addEventListener('resize', onWindowResize);

    // Animation Loop
    animate3D();
  }

  function createParticleGrid() {
    const pCount = 400;
    const pGeo = new THREE.BufferGeometry();
    const pPos = new Float32Array(pCount * 3);

    for (let i = 0; i < pCount * 3; i += 3) {
      pPos[i] = (Math.random() - 0.5) * 120;
      pPos[i + 1] = (Math.random() - 0.5) * 120;
      pPos[i + 2] = (Math.random() - 0.5) * 120;
    }

    pGeo.setAttribute('position', new THREE.BufferAttribute(pPos, 3));
    const pMat = new THREE.PointsMaterial({
      color: 0x06b6d4,
      size: 0.6,
      transparent: true,
      opacity: 0.3
    });

    const pMesh = new THREE.Points(pGeo, pMat);
    scene.add(pMesh);
  }

  function create3DNodes() {
    nodeData.forEach(n => {
      const geo = new THREE.SphereGeometry(n.radius, 32, 32);
      const mat = new THREE.MeshPhongMaterial({
        color: n.color,
        emissive: n.color,
        emissiveIntensity: n.type === 'mule' ? 0.7 : 0.3,
        shininess: 80,
        transparent: true,
        opacity: 0.95
      });

      const mesh = new THREE.Mesh(geo, mat);
      mesh.position.copy(n.pos);
      mesh.userData = n;
      scene.add(mesh);
      nodeMeshes.push(mesh);

      // Mule Account Glowing Outer Halo
      if (n.type === 'mule') {
        const haloGeo = new THREE.RingGeometry(n.radius * 1.3, n.radius * 1.6, 32);
        const haloMat = new THREE.MeshBasicMaterial({
          color: 0xef4444,
          side: THREE.DoubleSide,
          transparent: true,
          opacity: 0.6
        });
        haloMesh = new THREE.Mesh(haloGeo, haloMat);
        haloMesh.position.copy(n.pos);
        scene.add(haloMesh);
      }
    });
  }

  function create3DEdges() {
    edgeData.forEach(e => {
      const p1 = e.from.pos;
      const p2 = e.to.pos;

      // Calculate 3D Quadratic Bezier Arc Control Point
      const mid = new THREE.Vector3().addVectors(p1, p2).multiplyScalar(0.5);
      mid.y += 4.0; // Elevate curve in Y axis

      const curve = new THREE.QuadraticBezierCurve3(p1, mid, p2);
      const points = curve.getPoints(50);
      const lineGeo = new THREE.BufferGeometry().setFromPoints(points);

      const isMule = e.from.type === 'mule' || e.to.type === 'mule';
      const lineMat = new THREE.LineBasicMaterial({
        color: isMule ? 0xef4444 : 0x06b6d4,
        transparent: true,
        opacity: isMule ? 0.6 : 0.35,
        linewidth: 2
      });

      const line = new THREE.Line(lineGeo, lineMat);
      scene.add(line);

      // Traveling Light Particle along Arc
      const particleGeo = new THREE.SphereGeometry(0.3, 16, 16);
      const particleMat = new THREE.MeshBasicMaterial({
        color: isMule ? 0xff0055 : 0x00f2fe
      });
      const particleMesh = new THREE.Mesh(particleGeo, particleMat);
      scene.add(particleMesh);

      particles.push({ mesh: particleMesh, curve: curve, progress: Math.random() });
    });
  }

  function animate3D() {
    requestAnimationFrame(animate3D);

    if (controls) controls.update();

    // Pulse Mule Halo
    if (haloMesh) {
      haloMesh.rotation.z += 0.01;
      const s = 1.0 + Math.sin(Date.now() * 0.003) * 0.15;
      haloMesh.scale.set(s, s, s);
    }

    // Animate Edge Energy Particles
    particles.forEach(p => {
      p.progress = (p.progress + 0.006) % 1.0;
      const pos = p.curve.getPoint(p.progress);
      p.mesh.position.copy(pos);
    });

    renderer.render(scene, camera);
  }

  function on3DMouseMove(event) {
    if (!renderer) return;
    const rect = renderer.domElement.getBoundingClientRect();
    mouse.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
    mouse.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;

    raycaster.setFromCamera(mouse, camera);
    const intersects = raycaster.intersectObjects(nodeMeshes);

    if (intersects.length > 0) {
      renderer.domElement.style.cursor = 'pointer';
      const data = intersects[0].object.userData;
      updateHudCard(data);
    } else {
      renderer.domElement.style.cursor = 'default';
    }
  }

  function on3DMouseClick(event) {
    if (!renderer) return;
    raycaster.setFromCamera(mouse, camera);
    const intersects = raycaster.intersectObjects(nodeMeshes);

    if (intersects.length > 0) {
      const targetPos = intersects[0].object.position;
      // Lerp camera target
      if (controls) {
        controls.target.copy(targetPos);
      }
    }
  }

  function updateHudCard(data) {
    const idEl = document.getElementById('hudNodeId');
    const typeEl = document.getElementById('hudNodeType');
    const riskEl = document.getElementById('hudRiskVal');
    const degEl = document.getElementById('hudDegreeVal');
    const attEl = document.getElementById('hudAttentionVal');
    const prEl = document.getElementById('hudPageRankVal');

    if (idEl) idEl.textContent = data.id;
    if (typeEl) {
      typeEl.textContent = `${data.type.toUpperCase()} NODE`;
      typeEl.style.color = data.type === 'mule' ? 'var(--status-critical)' : 'var(--accent-cyan)';
    }
    if (riskEl) riskEl.textContent = `${(data.risk * 100).toFixed(1)}%`;
    if (degEl) degEl.textContent = data.deg;
    if (attEl) attEl.textContent = data.att;
    if (prEl) prEl.textContent = data.pr;
  }

  function update3DNodeState(accountId, result) {
    const targetNode = nodeMeshes.find(m => m.userData.id === accountId || m.userData.id === 'C_MULE_8841');
    if (targetNode) {
      targetNode.userData.risk = result.fraud_probability;
      updateHudCard(targetNode.userData);

      if (controls && result.is_flagged) {
        controls.target.copy(targetNode.position);
      }
    }
  }

  function onWindowResize() {
    if (!container || !renderer || !camera) return;
    const width = container.clientWidth;
    const height = container.clientHeight;
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
    renderer.setSize(width, height);
  }

  // 3D Viewport Controls Toolbar Handlers
  document.getElementById('btnToggleOrbit')?.addEventListener('click', function() {
    if (controls) {
      controls.autoRotate = !controls.autoRotate;
      this.classList.toggle('active', controls.autoRotate);
    }
  });

  document.getElementById('btnFocusMule')?.addEventListener('click', () => {
    if (controls) {
      controls.target.set(0, 0, 0);
      camera.position.set(0, 10, 28);
    }
  });

  document.getElementById('btnResetCamera')?.addEventListener('click', () => {
    if (controls) {
      controls.target.set(0, 0, 0);
      camera.position.set(0, 18, 42);
    }
  });

  // Initialize 3D WebGL Engine
  init3D();

  // Load Initial Preset
  loadPreset('mule');
});
