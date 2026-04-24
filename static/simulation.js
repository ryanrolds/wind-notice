    (function() {
      var wx = (window.WX_INITIAL) ? Object.assign({}, window.WX_INITIAL) : { cloud: 0, wind: 0, gust: 0, precip: 0, temp: 60, weather_code: 0 };
      var params = new URLSearchParams(window.location.search);
      if (params.get('cloud') !== null) {
        // URL parameter override mode
        wx = { cloud: parseFloat(params.get('cloud'))||0, wind: parseFloat(params.get('wind'))||0, gust: parseFloat(params.get('gust'))||0, precip: parseFloat(params.get('precip'))||0, temp: parseFloat(params.get('temp'))||60, weather_code: parseInt(params.get('weather_code'))||0 };
        wx.effectNames = params.get('effects') || '';
      } else if (params.get('random') === 'true') {
        wx = { cloud: Math.random()*100, wind: Math.random()*30, gust: Math.random()*45, precip: Math.random() < 0.3 ? 0.1 + Math.random()*0.4 : 0, temp: 45+Math.random()*35, weather_code: 0 };
        // Weighted multi-effect selection: 40% none, 30% one, 20% two, 10% three
        var roll = Math.random();
        var numEffects = roll < 0.4 ? 0 : roll < 0.7 ? 1 : roll < 0.9 ? 2 : 3;
        var effectPool = [95, 96, 71, 45, 51, 65, 66];
        // Shuffle pool
        for (var si = effectPool.length - 1; si > 0; si--) {
          var sj = Math.floor(Math.random() * (si + 1));
          var tmp = effectPool[si]; effectPool[si] = effectPool[sj]; effectPool[sj] = tmp;
        }
        wx.activeEffects = [];
        for (var ei = 0; ei < numEffects; ei++) wx.activeEffects.push(effectPool[ei]);
        if (numEffects > 0) wx.precip = Math.max(wx.precip, 0.1);
      }

      // Derive active effects
      var activeEffects = [];
      if (wx.effectNames !== undefined) {
        // URL parameter mode: effect names passed directly
        if (wx.effectNames) activeEffects = wx.effectNames.split(',');
      } else if (wx.activeEffects) {
        // Random mode: map codes to effect names
        var codeToEffects = function(code) {
          var e = [];
          if (code === 95 || code === 96 || code === 99) e.push('lightning');
          if (code === 96 || code === 99) e.push('hail');
          if ((code >= 71 && code <= 77) || (code >= 85 && code <= 86)) e.push('snow');
          if (code === 45 || code === 48) e.push('fog');
          if (code >= 51 && code <= 57) e.push('drizzle');
          if (code === 65 || code === 82) e.push('heavyrain');
          if (code === 66 || code === 67) e.push('freezingrain');
          if (code === 61 || code === 63 || (code >= 80 && code <= 81)) e.push('rain');
          return e;
        };
        for (var ae = 0; ae < wx.activeEffects.length; ae++) {
          var effs = codeToEffects(wx.activeEffects[ae]);
          for (var ef = 0; ef < effs.length; ef++) {
            if (activeEffects.indexOf(effs[ef]) === -1) activeEffects.push(effs[ef]);
          }
        }
      } else {
        // Normal mode: derive from single weather code
        var wc = wx.weather_code;
        if (wc === 95 || wc === 96 || wc === 99) activeEffects.push('lightning');
        if (wc === 96 || wc === 99) activeEffects.push('hail');
        if ((wc >= 71 && wc <= 77) || (wc >= 85 && wc <= 86)) activeEffects.push('snow');
        if (wc === 45 || wc === 48) activeEffects.push('fog');
        if (wc >= 51 && wc <= 57) activeEffects.push('drizzle');
        if (wc === 65 || wc === 82) activeEffects.push('heavyrain');
        if (wc === 66 || wc === 67) activeEffects.push('freezingrain');
        if (wc === 61 || wc === 63 || (wc >= 80 && wc <= 81)) activeEffects.push('rain');
      }

      // Horizon offset — the y-coordinate where the back wave sits. Fixed default
      // (185) keeps the main page layout stable; override via ?horizon=350 for
      // OG screenshots where we want the sky to take up more of the frame.
      var horizon = parseFloat(params.get('horizon'));
      if (isNaN(horizon)) horizon = 185;

      function hasEffect(name) { return activeEffects.indexOf(name) !== -1; }
      var isStormy = hasEffect('lightning');
      var isSnowy = hasEffect('snow');
      var isFoggy = hasEffect('fog');
      var isRainy = hasEffect('rain') || hasEffect('heavyrain') || hasEffect('drizzle') || hasEffect('freezingrain') || wx.precip > 0.1;
      var isPrecip = isRainy || isSnowy || hasEffect('hail');

      var c = document.getElementById('bg-canvas');
      var ctx = c.getContext('2d');
      var dpr = window.devicePixelRatio || 1;
      var W, H;
      function resize() {
        W = window.innerWidth;
        H = window.innerHeight;
        c.width = W * dpr;
        c.height = H * dpr;
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      }
      resize();
      window.addEventListener('resize', resize);

      var cloudFrac = wx.cloud / 100;
      if (isStormy) cloudFrac = Math.max(cloudFrac, 0.85);
      if (isFoggy) cloudFrac = Math.max(cloudFrac, 0.6);
      var windFactor = Math.min(wx.wind / 20, 1);

      function makeCloud() {
        var isBig = !!(arguments[2]);
        var numPuffs = isBig ? (7 + Math.floor(Math.random() * 3))
                             : (3 + Math.floor(Math.random() * 4));
        if (cloudFrac > 0.7) numPuffs += 2;
        var puffs = [];
        for (var i = 0; i < numPuffs; i++) {
          puffs.push([
            (i - numPuffs/2) * (18 + Math.random()*14),
            (Math.random() - 0.5) * 25,
            15 + Math.random() * 22 + (cloudFrac > 0.5 ? 5 : 0) + (isBig ? 6 : 0)
          ]);
        }
        var slot = arguments[0], totalSlots = arguments[1] || 1;
        var slotW = (W || 1200) / totalSlots;
        var jitter = (isBig ? Math.random() * 0.3 : (Math.random() - 0.5) * 0.5);
        var baseX = slot * slotW + slotW * 0.5 + jitter * slotW;
        var scale = isBig
          ? 1.35 + Math.random() * 0.35
          : 0.55 + Math.random() * 0.7 + cloudFrac * 0.3;
        return {
          x: baseX - 40 * scale,
          y: (isBig ? 55 : 35) + Math.random() * 60,
          s: scale,
          speed: 3 + Math.random() * 8 + windFactor * 10,
          puffs: puffs,
          color: ''
        };
      }
      var numClouds = cloudFrac < 0.05 ? 0 : Math.round(cloudFrac * 10 + Math.random() * 2);
      var clouds = [];
      if (numClouds > 0) {
        var slots = Math.max(numClouds, 3);
        // First cloud is a big feature cloud placed on the left side.
        clouds.push(makeCloud(0, slots, true));
        for (var ci = 1; ci < numClouds; ci++) clouds.push(makeCloud(ci, slots, false));
      }
      clouds.sort(function(a, b) { return a.y - b.y; });
      for (var ci = 0; ci < clouds.length; ci++) {
        var frac = clouds.length > 1 ? ci / (clouds.length - 1) : 1;
        var lo = isPrecip ? 170 : (cloudFrac > 0.7 ? 205 : 230);
        var hi = isPrecip ? 210 : (cloudFrac > 0.7 ? 240 : 255);
        var shade = Math.round(lo + frac * (hi - lo));
        clouds[ci].color = 'rgb(' + shade + ',' + shade + ',' + shade + ')';
      }

      // --- Rain init ---
      var rainDrops = [];
      function initRain() {
        if (!hasEffect('rain') && !isRainy) return;
        if (hasEffect('heavyrain') || hasEffect('drizzle') || hasEffect('freezingrain')) return;
        var numDrops = Math.round(80 + wx.precip * 400);
        for (var ri = 0; ri < numDrops; ri++) {
          rainDrops.push({ x: Math.random() * W, y: Math.random() * H, len: 8 + Math.random() * 14, speed: 300 + Math.random() * 200 });
        }
      }
      initRain();

      // --- Drizzle init ---
      var drizzleDrops = [];
      function initDrizzle() {
        if (!hasEffect('drizzle')) return;
        var numDrops = Math.round(60 + wx.precip * 200);
        for (var i = 0; i < numDrops; i++) {
          drizzleDrops.push({ x: Math.random() * W, y: Math.random() * H, len: 4 + Math.random() * 6, speed: 150 + Math.random() * 100 });
        }
      }
      initDrizzle();

      // --- Heavy rain init ---
      var heavyDrops = [];
      var splashes = [];
      function initHeavyRain() {
        if (!hasEffect('heavyrain')) return;
        var numDrops = Math.round(200 + wx.precip * 600);
        for (var i = 0; i < numDrops; i++) {
          heavyDrops.push({ x: Math.random() * W, y: Math.random() * H, len: 14 + Math.random() * 18, speed: 450 + Math.random() * 250 });
        }
      }
      initHeavyRain();

      // --- Freezing rain init ---
      var freezeDrops = [];
      function initFreezingRain() {
        if (!hasEffect('freezingrain')) return;
        var numDrops = Math.round(80 + wx.precip * 400);
        for (var i = 0; i < numDrops; i++) {
          freezeDrops.push({ x: Math.random() * W, y: Math.random() * H, len: 8 + Math.random() * 14, speed: 300 + Math.random() * 200 });
        }
      }
      initFreezingRain();

      // --- Snow init ---
      var snowFlakes = [];
      function initSnow() {
        if (!hasEffect('snow')) return;
        var numFlakes = Math.round(60 + Math.random() * 40);
        for (var i = 0; i < numFlakes; i++) {
          snowFlakes.push({ x: Math.random() * W, y: Math.random() * 220 - 20, r: 1.5 + Math.random() * 3, speed: 30 + Math.random() * 40, drift: (Math.random() - 0.3) * 0.8 });
        }
      }
      initSnow();

      // --- Hail init ---
      var hailStones = [];
      function initHail() {
        if (!hasEffect('hail')) return;
        var numStones = Math.round(30 + Math.random() * 20);
        for (var i = 0; i < numStones; i++) {
          hailStones.push({ x: Math.random() * W, y: Math.random() * 210 - 15, r: 2 + Math.random() * 3, vy: 200 + Math.random() * 150, vx: 0, bouncing: false });
        }
      }
      initHail();

      // --- Lightning state ---
      var lightningFlash = 0;
      var lightningBolts = [];
      var lightningTimer = 3 + Math.random() * 5;
      function makeBolt() {
        var x = 50 + Math.random() * (W ? W - 100 : 600);
        var yStart = 20 + Math.random() * 40;
        var yEnd = 170 + Math.random() * 30;
        var segments = [];
        var cx = x, cy = yStart;
        var steps = 6 + Math.floor(Math.random() * 5);
        for (var i = 0; i < steps; i++) {
          var nx = cx + (Math.random() - 0.5) * 40;
          var ny = cy + (yEnd - yStart) / steps;
          segments.push([cx, cy, nx, ny]);
          cx = nx; cy = ny;
        }
        return { segments: segments, life: 0.3, age: 0 };
      }

      // --- Fog state ---
      var fogOffset1 = Math.random() * 1000;
      var fogOffset2 = Math.random() * 1000;

      function lerpHex(a, b, t) {
        var ar = parseInt(a.slice(1,3),16), ag = parseInt(a.slice(3,5),16), ab = parseInt(a.slice(5,7),16);
        var br = parseInt(b.slice(1,3),16), bg = parseInt(b.slice(3,5),16), bb = parseInt(b.slice(5,7),16);
        var r = Math.round(ar+(br-ar)*t), g = Math.round(ag+(bg-ag)*t), bl = Math.round(ab+(bb-ab)*t);
        return '#'+((1<<24)|(r<<16)|(g<<8)|bl).toString(16).slice(1);
      }

      // Bird scheduling state. First appearance is random 20-60s after load so
      // the bird never shows up at t=0 mid-flight, and it always enters from
      // off the left/right edge rather than popping into view on screen.
      var birdCrossDur = 28;  // seconds to cross (slower than before)
      var birdInFlight = false;
      var birdStartAt = 0;
      var birdDirection = Math.random() < 0.5 ? 1 : -1;
      var nextBirdAt = 20 + Math.random() * 40;

      var t = 0;
      function draw() {
        t += 0.016;
        ctx.clearRect(0, 0, W, H);

        // --- Sky gradient ---
        var sky = ctx.createLinearGradient(0, 0, 0, H);
        if (isStormy) {
          sky.addColorStop(0, '#546e7a');
          sky.addColorStop(0.4, '#78909c');
          sky.addColorStop(1, '#90a4ae');
        } else if (isSnowy) {
          sky.addColorStop(0, '#b0bec5');
          sky.addColorStop(0.3, '#cfd8dc');
          sky.addColorStop(0.6, '#e0e0e0');
          sky.addColorStop(1, '#eceff1');
        } else if (isFoggy) {
          sky.addColorStop(0, '#90a4ae');
          sky.addColorStop(0.3, '#b0bec5');
          sky.addColorStop(0.6, '#cfd8dc');
          sky.addColorStop(1, '#e0e0e0');
        } else if (isPrecip) {
          sky.addColorStop(0, '#78909c');
          sky.addColorStop(0.4, '#90a4ae');
          sky.addColorStop(1, '#b0bec5');
        } else {
          var clearSky = ['#1976d2','#42a5f5','#90caf9','#bbdefb'];
          var partSky  = ['#64b5f6','#90caf9','#bbdefb','#e3f2fd'];
          var overSky  = ['#90a4ae','#b0bec5','#cfd8dc','#eceff1'];
          var skyStops = [0, 0.3, 0.6, 1];
          var palA, palB, blend;
          if (cloudFrac <= 0.5) {
            palA = clearSky; palB = partSky;
            blend = cloudFrac / 0.5;
          } else {
            palA = partSky; palB = overSky;
            blend = (cloudFrac - 0.5) / 0.5;
          }
          for (var i = 0; i < 4; i++) {
            sky.addColorStop(skyStops[i], lerpHex(palA[i], palB[i], blend));
          }
        }
        ctx.fillStyle = sky;
        ctx.fillRect(0, 0, W, H);

        // --- Lightning flash overlay ---
        if (lightningFlash > 0) {
          ctx.fillStyle = 'rgba(255,255,255,' + (lightningFlash * 0.6) + ')';
          ctx.fillRect(0, 0, W, H);
          lightningFlash -= 0.016 * 4;
          if (lightningFlash < 0) lightningFlash = 0;
        }

        // --- Sun ---
        if (cloudFrac < 0.7 && !isStormy && !isFoggy) {
          var sunAlpha = Math.min(1, (1 - cloudFrac / 0.7));
          ctx.save();
          // Glow
          ctx.beginPath();
          ctx.arc(W * 0.82, 55, 45, 0, Math.PI * 2);
          ctx.fillStyle = 'rgba(255,235,130,' + (sunAlpha * 0.15) + ')';
          ctx.fill();
          // Disc
          ctx.beginPath();
          ctx.arc(W * 0.82, 55, 22, 0, Math.PI * 2);
          ctx.fillStyle = 'rgba(255,220,80,' + sunAlpha + ')';
          ctx.shadowColor = 'rgba(255,200,50,' + (sunAlpha * 0.8) + ')';
          ctx.shadowBlur = 30;
          ctx.fill();
          ctx.restore();
        }

        // --- Clouds ---
        for (var i = 0; i < clouds.length; i++) {
          var cl = clouds[i];
          cl.x += cl.speed * 0.016;
          if (cl.x > W + 100) cl.x = -140 * cl.s;
          ctx.save();
          ctx.translate(cl.x, cl.y);
          ctx.scale(cl.s, cl.s);
          ctx.fillStyle = cl.color;
          for (var p = 0; p < cl.puffs.length; p++) {
            ctx.beginPath();
            ctx.arc(cl.puffs[p][0], cl.puffs[p][1], cl.puffs[p][2], 0, Math.PI*2);
            ctx.fill();
          }
          ctx.restore();
        }

        // --- Bird ---
        // Distant seagull silhouette, scheduled with random gaps between
        // appearances. Always enters from off-screen; never visible at load
        // time. Suppressed in heavy precip / storm conditions.
        var birdAllowed = !isStormy && !hasEffect('heavyrain') && !hasEffect('hail');
        if (birdAllowed) {
          if (!birdInFlight && t >= nextBirdAt) {
            birdInFlight = true;
            birdStartAt = t;
            birdDirection = Math.random() < 0.5 ? 1 : -1;
          }
          if (birdInFlight) {
            var elapsed = t - birdStartAt;
            if (elapsed >= birdCrossDur) {
              birdInFlight = false;
              // 45-120s quiet gap before the next bird.
              nextBirdAt = t + 45 + Math.random() * 75;
            } else {
              var birdPhase = elapsed / birdCrossDur; // 0..1
              var bx = birdDirection === 1
                ? -40 + birdPhase * (W + 80)
                : (W + 40) - birdPhase * (W + 80);
              var bobY = Math.max(60, horizon * 0.55);
              var by = bobY + Math.sin(t * 0.9) * 5 + Math.sin(t * 0.4) * 3;
              var flap = 0.55 + Math.sin(t * 7.5) * 0.45;
              var wingSpan = 12;
              var wingDip = 5 * flap;
              ctx.save();
              ctx.strokeStyle = isFoggy ? 'rgba(110,118,130,0.4)' : 'rgba(60,70,85,0.55)';
              ctx.lineWidth = 1.3;
              ctx.lineCap = 'round';
              ctx.lineJoin = 'round';
              ctx.beginPath();
              ctx.moveTo(bx - wingSpan, by);
              ctx.quadraticCurveTo(bx - wingSpan * 0.5, by - wingDip, bx, by);
              ctx.quadraticCurveTo(bx + wingSpan * 0.5, by - wingDip, bx + wingSpan, by);
              ctx.stroke();
              ctx.restore();
            }
          }
        }

        // --- Lightning bolts ---
        if (hasEffect('lightning')) {
          lightningTimer -= 0.016;
          if (lightningTimer <= 0) {
            lightningBolts.push(makeBolt());
            lightningFlash = 1;
            lightningTimer = 3 + Math.random() * 5;
          }
          for (var li = lightningBolts.length - 1; li >= 0; li--) {
            var bolt = lightningBolts[li];
            bolt.age += 0.016;
            if (bolt.age > bolt.life) { lightningBolts.splice(li, 1); continue; }
            var alpha = 1 - bolt.age / bolt.life;
            ctx.save();
            ctx.shadowColor = 'rgba(200,220,255,0.8)';
            ctx.shadowBlur = 15;
            ctx.strokeStyle = 'rgba(255,255,255,' + alpha + ')';
            ctx.lineWidth = 2.5;
            ctx.beginPath();
            for (var seg = 0; seg < bolt.segments.length; seg++) {
              var s = bolt.segments[seg];
              ctx.moveTo(s[0], s[1]);
              ctx.lineTo(s[2], s[3]);
            }
            ctx.stroke();
            ctx.restore();
          }
        }

        // --- Fog background layer (behind waves) ---
        if (hasEffect('fog')) {
          fogOffset1 += 0.3;
          fogOffset2 += 0.5;
          ctx.save();
          var fogGrad = ctx.createLinearGradient(0, 100, 0, 220);
          fogGrad.addColorStop(0, 'rgba(200,210,220,0)');
          fogGrad.addColorStop(0.4, 'rgba(200,210,220,0.35)');
          fogGrad.addColorStop(1, 'rgba(200,210,220,0.5)');
          ctx.fillStyle = fogGrad;
          ctx.fillRect(0, 100, W, 120);
          ctx.restore();
        }

        // --- Wave helpers ---
        var waveAmpScale = 0.5 + windFactor * 1.0;
        var waveSpeedScale = 0.6 + windFactor * 0.8;
        var waterColors;
        if (isSnowy) {
          waterColors = ['#455a64','#37474f','#263238'];
        } else if (isPrecip || isStormy) {
          waterColors = ['#37474f','#263238','#1a2327'];
        } else {
          waterColors = ['#1565c0','#0d47a1','#0a3d91'];
        }
        function drawWaveLayer(idx) {
          var amp = (8 - idx * 1.5) * waveAmpScale;
          var yBase = horizon + idx * 12;
          var speed = (1 + idx * 0.4) * waveSpeedScale;
          ctx.fillStyle = waterColors[idx];
          ctx.beginPath();
          ctx.moveTo(0, H);
          var phaseOff = idx * 2.1;
          for (var x = 0; x <= W; x += 4) {
            ctx.lineTo(x, yBase + Math.sin(x*0.015 + t*speed + phaseOff)*amp + Math.sin(x*0.008 + t*speed*0.6 + phaseOff)*amp*0.5);
          }
          ctx.lineTo(W, H);
          ctx.closePath();
          ctx.fill();
        }

        function waveY(x) {
          var a = 6.5 * waveAmpScale;
          var s = 1.4 * waveSpeedScale;
          return (horizon + 12) + Math.sin(x*0.015 + t*s + 2.1)*a + Math.sin(x*0.008 + t*s*0.6 + 2.1)*a*0.5;
        }

        function drawBoat() {
          var boatX = W * 0.75;
          var bob = Math.sin(t * 2.2) * 3;
          var boatY = waveY(boatX) - 23 + bob;
          var dx = 4;
          var tilt = Math.atan2(waveY(boatX + dx) - waveY(boatX - dx), dx * 2);
          ctx.save();
          ctx.translate(boatX, boatY);
          ctx.rotate(tilt);

          ctx.strokeStyle = 'rgba(255,255,255,0.9)';
          ctx.lineWidth = 2;
          ctx.beginPath();
          ctx.moveTo(0, -75);
          ctx.lineTo(0, 18);
          ctx.stroke();

          var pennantLen = 20;
          var pennantSegs = 10;
          var windLean = Math.min(0.15 + windFactor * 1.3, 1);
          var pennantAngle = (1 - windLean) * Math.PI / 2;
          var flutterFreq = 3 + windFactor * 10;
          var flutterAmp = 0.8 + windFactor * 3.5;
          var pca = Math.cos(pennantAngle), psa = Math.sin(pennantAngle);
          ctx.strokeStyle = '#e53935';
          ctx.lineWidth = 2;
          ctx.beginPath();
          ctx.moveTo(0, -75);
          for (var pni = 1; pni <= pennantSegs; pni++) {
            var pf = pni / pennantSegs;
            var pdist = pf * pennantLen;
            var flutter = Math.sin(t * flutterFreq - pf * 3) * flutterAmp * pf;
            ctx.lineTo(pca * pdist - psa * flutter, -75 + psa * pdist + pca * flutter);
          }
          ctx.stroke();

          ctx.fillStyle = 'rgba(255,255,255,0.92)';
          ctx.beginPath();
          ctx.moveTo(0, -72);
          ctx.lineTo(0, 15);
          ctx.lineTo(-34, 15);
          ctx.closePath();
          ctx.fill();

          ctx.fillStyle = 'rgba(255,255,255,0.7)';
          ctx.beginPath();
          ctx.moveTo(0, -58);
          ctx.lineTo(0, 10);
          ctx.lineTo(24, 10);
          ctx.closePath();
          ctx.fill();

          ctx.fillStyle = '#e53935';
          ctx.beginPath();
          ctx.moveTo(-36, 17);
          ctx.lineTo(36, 17);
          ctx.quadraticCurveTo(42, 30, 32, 33);
          ctx.lineTo(-32, 33);
          ctx.quadraticCurveTo(-42, 30, -36, 17);
          ctx.closePath();
          ctx.fill();
          ctx.restore();
        }

        // --- Draw order: wave0, boat, wave1, precipitation, wave2, fog foreground ---
        drawWaveLayer(0);
        drawBoat();
        drawWaveLayer(1);

        var windAngle = windFactor * 2;

        // --- Draw rain ---
        if (rainDrops.length > 0) {
          ctx.strokeStyle = 'rgba(200,210,220,0.4)';
          ctx.lineWidth = 1;
          for (var ri = 0; ri < rainDrops.length; ri++) {
            var rd = rainDrops[ri];
            rd.y += rd.speed * 0.016;
            rd.x += windAngle * rd.speed * 0.008;
            if (rd.y > H) { rd.y = -rd.len; rd.x = Math.random() * W; }
            if (rd.x > W) rd.x -= W;
            ctx.beginPath();
            ctx.moveTo(rd.x, rd.y);
            ctx.lineTo(rd.x + windAngle * rd.len * 0.3, rd.y + rd.len);
            ctx.stroke();
          }
        }

        // --- Draw drizzle ---
        if (drizzleDrops.length > 0) {
          ctx.strokeStyle = 'rgba(200,210,220,0.25)';
          ctx.lineWidth = 0.5;
          for (var di = 0; di < drizzleDrops.length; di++) {
            var dd = drizzleDrops[di];
            dd.y += dd.speed * 0.016;
            dd.x += windAngle * dd.speed * 0.005;
            if (dd.y > H) { dd.y = -dd.len; dd.x = Math.random() * W; }
            if (dd.x > W) dd.x -= W;
            ctx.beginPath();
            ctx.moveTo(dd.x, dd.y);
            ctx.lineTo(dd.x + windAngle * dd.len * 0.2, dd.y + dd.len);
            ctx.stroke();
          }
        }

        // --- Draw heavy rain + splashes ---
        if (heavyDrops.length > 0) {
          ctx.strokeStyle = 'rgba(200,210,220,0.5)';
          ctx.lineWidth = 2;
          for (var hi = 0; hi < heavyDrops.length; hi++) {
            var hd = heavyDrops[hi];
            hd.y += hd.speed * 0.016;
            hd.x += windAngle * hd.speed * 0.008;
            var surfY = waveY(hd.x);
            if (hd.y > surfY) {
              splashes.push({ x: hd.x, y: surfY, r: 0, maxR: 4 + Math.random() * 4, speed: 30 + Math.random() * 20 });
              hd.y = -hd.len; hd.x = Math.random() * W;
            }
            if (hd.x > W) hd.x -= W;
            ctx.beginPath();
            ctx.moveTo(hd.x, hd.y);
            ctx.lineTo(hd.x + windAngle * hd.len * 0.3, hd.y + hd.len);
            ctx.stroke();
          }
          // Draw splashes
          for (var si = splashes.length - 1; si >= 0; si--) {
            var sp = splashes[si];
            sp.r += sp.speed * 0.016;
            if (sp.r > sp.maxR) { splashes.splice(si, 1); continue; }
            var alpha = 1 - sp.r / sp.maxR;
            ctx.strokeStyle = 'rgba(200,210,220,' + (alpha * 0.5) + ')';
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.arc(sp.x, sp.y, sp.r, Math.PI, 0);
            ctx.stroke();
          }
        }

        // --- Draw freezing rain ---
        if (freezeDrops.length > 0) {
          ctx.strokeStyle = 'rgba(150,200,240,0.5)';
          ctx.lineWidth = 1;
          for (var fi = 0; fi < freezeDrops.length; fi++) {
            var fd = freezeDrops[fi];
            fd.y += fd.speed * 0.016;
            fd.x += windAngle * fd.speed * 0.008;
            if (fd.y > H) { fd.y = -fd.len; fd.x = Math.random() * W; }
            if (fd.x > W) fd.x -= W;
            ctx.beginPath();
            ctx.moveTo(fd.x, fd.y);
            ctx.lineTo(fd.x + windAngle * fd.len * 0.3, fd.y + fd.len);
            ctx.stroke();
          }
        }

        // --- Draw snow ---
        if (snowFlakes.length > 0) {
          ctx.fillStyle = 'rgba(255,255,255,0.8)';
          for (var sni = 0; sni < snowFlakes.length; sni++) {
            var sf = snowFlakes[sni];
            sf.y += sf.speed * 0.016;
            sf.x += sf.drift + windFactor * 0.5;
            var surfY = waveY(sf.x);
            if (sf.y > surfY) { sf.y = -5; sf.x = Math.random() * W; }
            if (sf.x > W) sf.x -= W;
            if (sf.x < 0) sf.x += W;
            ctx.beginPath();
            ctx.arc(sf.x, sf.y, sf.r, 0, Math.PI * 2);
            ctx.fill();
          }
        }

        // --- Draw hail (bounces off boat, resets on waves) ---
        if (hailStones.length > 0) {
          var boatX = W * 0.75;
          var boatBob = Math.sin(t * 2.2) * 3;
          var boatDeckY = waveY(boatX) - 23 + boatBob + 17;
          for (var hi2 = 0; hi2 < hailStones.length; hi2++) {
            var hs = hailStones[hi2];
            if (hs.bouncing) {
              hs.vy += 400 * 0.016; // gravity
              hs.y += hs.vy * 0.016;
              hs.x += hs.vx * 0.016;
              if (hs.y > H + 20) {
                hs.x = Math.random() * W;
                hs.y = -5;
                hs.vy = 200 + Math.random() * 150;
                hs.vx = 0;
                hs.bouncing = false;
              }
            } else {
              hs.y += hs.vy * 0.016;
              hs.x += windFactor * 1.5;
              // Check if hitting boat deck
              if (hs.x > boatX - 36 && hs.x < boatX + 36 && hs.y > boatDeckY) {
                hs.bouncing = true;
                hs.vy = -(80 + Math.random() * 60);
                hs.vx = (Math.random() - 0.5) * 40;
                hs.y = boatDeckY;
              } else {
                var surfY = waveY(hs.x);
                if (hs.y > surfY) {
                  hs.x = Math.random() * W;
                  hs.y = -5;
                  hs.vy = 200 + Math.random() * 150;
                  hs.vx = 0;
                }
              }
            }
            if (hs.x > W) hs.x -= W;
            if (hs.x < 0) hs.x += W;
            ctx.fillStyle = 'rgba(220,230,240,0.85)';
            ctx.beginPath();
            ctx.arc(hs.x, hs.y, hs.r, 0, Math.PI * 2);
            ctx.fill();
          }
        }

        // --- Front wave ---
        drawWaveLayer(2);

        // --- Fog foreground overlay ---
        if (hasEffect('fog')) {
          ctx.save();
          var fogFG = ctx.createLinearGradient(0, 140, 0, 220);
          fogFG.addColorStop(0, 'rgba(200,210,220,0)');
          fogFG.addColorStop(0.5, 'rgba(200,210,220,0.12)');
          fogFG.addColorStop(1, 'rgba(200,210,220,0.12)');
          ctx.fillStyle = fogFG;
          ctx.fillRect(0, 140, W, H - 140);
          ctx.restore();
        }

        requestAnimationFrame(draw);
      }
      draw();

      document.addEventListener('DOMContentLoaded', function() {
        var dbg = document.getElementById('wx-debug');
        if (dbg) {
          var qs = '?cloud=' + wx.cloud.toFixed(0) + '&wind=' + wx.wind.toFixed(1) + '&gust=' + wx.gust.toFixed(1) + '&precip=' + wx.precip.toFixed(2) + '&temp=' + wx.temp.toFixed(1);
          if (activeEffects.length) qs += '&effects=' + activeEffects.join(',');
          dbg.textContent = qs + ' ';
          var rlink = document.createElement('a');
          rlink.href = '?random=true';
          rlink.textContent = 'Randomize';
          rlink.style.color = 'rgba(255,255,255,0.45)';
          dbg.appendChild(rlink);
        }
      });
    })();
