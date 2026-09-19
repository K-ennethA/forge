/* glbview.js — the Workspace's live model view.
 *
 * "It's almost like we should have a simplified blender window open within
 * forge."  This is that window: a .glb exported out of the running Blender,
 * drawn here, orbitable, with the rig's animation playable on it.
 *
 * Written from nothing rather than vendored from three.js, for the reason
 * every other file on this page is: Forge runs on a machine that is often
 * offline and always without a build step, so a viewer that needed a CDN — or
 * a bundler — would be the one panel that could not do its job.  What it does
 * is deliberately narrow:
 *
 *   * glTF 2.0 binary only (.glb), positions, normals and indices;
 *   * the node hierarchy, so a scene of several objects lands where Blender
 *     had it;
 *   * linear-blend skinning on the CPU, so a rigged character actually MOVES
 *     when its walk cycle is played rather than standing still while the
 *     armature waves about invisibly;
 *   * orbit, pan and zoom, because a model you cannot turn around is a
 *     picture, and there is already a panel full of pictures.
 *
 * What it is not: a renderer.  There are no materials, no textures and no
 * shadows — one clay shader, the same grey the Workbench previews use, lit
 * from the camera.  Looking at the SHAPE is the job; the renders folder is
 * where the pictures live.
 */
(function (global) {
  "use strict";

  // ------------------------------------------------------------- matrices --
  //
  // Column-major, the order WebGL wants, so nothing is transposed on its way
  // to a uniform.

  function identity(out) {
    out = out || new Float32Array(16);
    out[0] = 1; out[1] = 0; out[2] = 0; out[3] = 0;
    out[4] = 0; out[5] = 1; out[6] = 0; out[7] = 0;
    out[8] = 0; out[9] = 0; out[10] = 1; out[11] = 0;
    out[12] = 0; out[13] = 0; out[14] = 0; out[15] = 1;
    return out;
  }

  function multiply(out, a, b) {
    var a00 = a[0], a01 = a[1], a02 = a[2], a03 = a[3],
        a10 = a[4], a11 = a[5], a12 = a[6], a13 = a[7],
        a20 = a[8], a21 = a[9], a22 = a[10], a23 = a[11],
        a30 = a[12], a31 = a[13], a32 = a[14], a33 = a[15];
    for (var i = 0; i < 4; i++) {
      var b0 = b[i * 4], b1 = b[i * 4 + 1], b2 = b[i * 4 + 2], b3 = b[i * 4 + 3];
      out[i * 4] = b0 * a00 + b1 * a10 + b2 * a20 + b3 * a30;
      out[i * 4 + 1] = b0 * a01 + b1 * a11 + b2 * a21 + b3 * a31;
      out[i * 4 + 2] = b0 * a02 + b1 * a12 + b2 * a22 + b3 * a32;
      out[i * 4 + 3] = b0 * a03 + b1 * a13 + b2 * a23 + b3 * a33;
    }
    return out;
  }

  function fromTRS(out, t, r, s) {
    var x = r[0], y = r[1], z = r[2], w = r[3];
    var x2 = x + x, y2 = y + y, z2 = z + z;
    var xx = x * x2, xy = x * y2, xz = x * z2;
    var yy = y * y2, yz = y * z2, zz = z * z2;
    var wx = w * x2, wy = w * y2, wz = w * z2;
    out[0] = (1 - (yy + zz)) * s[0];
    out[1] = (xy + wz) * s[0];
    out[2] = (xz - wy) * s[0];
    out[3] = 0;
    out[4] = (xy - wz) * s[1];
    out[5] = (1 - (xx + zz)) * s[1];
    out[6] = (yz + wx) * s[1];
    out[7] = 0;
    out[8] = (xz + wy) * s[2];
    out[9] = (yz - wx) * s[2];
    out[10] = (1 - (xx + yy)) * s[2];
    out[11] = 0;
    out[12] = t[0]; out[13] = t[1]; out[14] = t[2]; out[15] = 1;
    return out;
  }

  function perspective(out, fovy, aspect, near, far) {
    var f = 1.0 / Math.tan(fovy / 2);
    identity(out);
    out[0] = f / aspect;
    out[5] = f;
    out[10] = (far + near) / (near - far);
    out[11] = -1;
    out[14] = (2 * far * near) / (near - far);
    out[15] = 0;
    return out;
  }

  function lookAt(out, eye, centre, up) {
    var zx = eye[0] - centre[0], zy = eye[1] - centre[1], zz = eye[2] - centre[2];
    var len = Math.hypot(zx, zy, zz) || 1;
    zx /= len; zy /= len; zz /= len;
    var xx = up[1] * zz - up[2] * zy,
        xy = up[2] * zx - up[0] * zz,
        xz = up[0] * zy - up[1] * zx;
    len = Math.hypot(xx, xy, xz) || 1;
    xx /= len; xy /= len; xz /= len;
    var yx = zy * xz - zz * xy,
        yy = zz * xx - zx * xz,
        yz = zx * xy - zy * xx;
    out[0] = xx; out[1] = yx; out[2] = zx; out[3] = 0;
    out[4] = xy; out[5] = yy; out[6] = zy; out[7] = 0;
    out[8] = xz; out[9] = yz; out[10] = zz; out[11] = 0;
    out[12] = -(xx * eye[0] + xy * eye[1] + xz * eye[2]);
    out[13] = -(yx * eye[0] + yy * eye[1] + yz * eye[2]);
    out[14] = -(zx * eye[0] + zy * eye[1] + zz * eye[2]);
    out[15] = 1;
    return out;
  }

  function slerp(out, a, b, t) {
    var ax = a[0], ay = a[1], az = a[2], aw = a[3];
    var bx = b[0], by = b[1], bz = b[2], bw = b[3];
    var cosom = ax * bx + ay * by + az * bz + aw * bw;
    if (cosom < 0) { cosom = -cosom; bx = -bx; by = -by; bz = -bz; bw = -bw; }
    var scale0, scale1;
    if (1 - cosom > 1e-6) {
      var omega = Math.acos(cosom), sinom = Math.sin(omega);
      scale0 = Math.sin((1 - t) * omega) / sinom;
      scale1 = Math.sin(t * omega) / sinom;
    } else {
      scale0 = 1 - t;
      scale1 = t;
    }
    out[0] = scale0 * ax + scale1 * bx;
    out[1] = scale0 * ay + scale1 * by;
    out[2] = scale0 * az + scale1 * bz;
    out[3] = scale0 * aw + scale1 * bw;
    return out;
  }

  // --------------------------------------------------------- reading a glb --

  var COMPONENTS = { SCALAR: 1, VEC2: 2, VEC3: 3, VEC4: 4, MAT2: 4, MAT3: 9, MAT4: 16 };
  var ARRAYS = {
    5120: Int8Array, 5121: Uint8Array, 5122: Int16Array,
    5123: Uint16Array, 5125: Uint32Array, 5126: Float32Array
  };
  //: What a normalised integer attribute divides by (glTF 3.9.2.2). Joint
  //: weights arrive as unsigned bytes out of Blender more often than not.
  var NORMALISERS = { 5120: 127, 5121: 255, 5122: 32767, 5123: 65535 };

  /** Split a .glb into its JSON chunk and its binary chunk. */
  function parseGLB(buffer) {
    var view = new DataView(buffer);
    if (buffer.byteLength < 12) {
      throw new Error("That file is too short to be a .glb.");
    }
    if (view.getUint32(0, true) !== 0x46546c67) {   // "glTF"
      throw new Error("That file does not start with the glTF magic number.");
    }
    var version = view.getUint32(4, true);
    if (version !== 2) {
      throw new Error("This viewer reads glTF 2.0; that file says version " +
                      version + ".");
    }
    var offset = 12, json = null, bin = null;
    while (offset + 8 <= buffer.byteLength) {
      var length = view.getUint32(offset, true);
      var kind = view.getUint32(offset + 4, true);
      var start = offset + 8;
      if (start + length > buffer.byteLength) { break; }
      if (kind === 0x4e4f534a) {                    // "JSON"
        json = JSON.parse(new TextDecoder("utf-8")
          .decode(new Uint8Array(buffer, start, length)));
      } else if (kind === 0x004e4942) {             // "BIN"
        bin = new Uint8Array(buffer, start, length);
      }
      offset = start + length + ((4 - (length % 4)) % 4);
    }
    if (!json) { throw new Error("That .glb has no JSON chunk in it."); }
    return { json: json, bin: bin };
  }

  /** One accessor as a flat typed array, de-interleaved if it has to be. */
  function readAccessor(gltf, bin, index) {
    var accessor = (gltf.accessors || [])[index];
    if (!accessor) { return null; }
    var size = COMPONENTS[accessor.type] || 1;
    var Kind = ARRAYS[accessor.componentType];
    if (!Kind) { return null; }
    var count = accessor.count | 0;
    if (accessor.bufferView === undefined) {
      return { data: new Kind(count * size), size: size, count: count,
               componentType: accessor.componentType };
    }
    var viewSpec = (gltf.bufferViews || [])[accessor.bufferView];
    if (!viewSpec || !bin) { return null; }
    var base = (bin.byteOffset + (viewSpec.byteOffset || 0) +
                (accessor.byteOffset || 0));
    var stride = viewSpec.byteStride || 0;
    var packed = size * Kind.BYTES_PER_ELEMENT;
    var out;
    if (!stride || stride === packed) {
      // The common case out of Blender: tightly packed, so this is a view on
      // the same bytes rather than a copy of them.
      out = new Kind(bin.buffer, base, count * size);
    } else {
      out = new Kind(count * size);
      for (var i = 0; i < count; i++) {
        var row = new Kind(bin.buffer, base + i * stride, size);
        out.set(row, i * size);
      }
    }
    return { data: out, size: size, count: count,
             componentType: accessor.componentType };
  }

  /** The same accessor as floats, un-normalising integers on the way. */
  function readFloats(gltf, bin, index) {
    var read = readAccessor(gltf, bin, index);
    if (!read) { return null; }
    if (read.data instanceof Float32Array) { return read; }
    var divisor = NORMALISERS[read.componentType] || 1;
    var accessor = (gltf.accessors || [])[index] || {};
    var scale = accessor.normalized ? divisor : 1;
    var out = new Float32Array(read.data.length);
    for (var i = 0; i < read.data.length; i++) { out[i] = read.data[i] / scale; }
    return { data: out, size: read.size, count: read.count,
             componentType: 5126 };
  }

  // ---------------------------------------------------------- scene graph --

  function buildNodes(gltf) {
    var raw = gltf.nodes || [];
    var nodes = raw.map(function (node, index) {
      var entry = {
        index: index,
        name: node.name || ("node" + index),
        children: node.children || [],
        mesh: node.mesh,
        skin: node.skin,
        parent: -1,
        translation: (node.translation || [0, 0, 0]).slice(),
        rotation: (node.rotation || [0, 0, 0, 1]).slice(),
        scale: (node.scale || [1, 1, 1]).slice(),
        matrix: node.matrix ? new Float32Array(node.matrix) : null,
        local: new Float32Array(16),
        world: new Float32Array(16)
      };
      // Kept so the Play toggle can put a node back exactly where the bind
      // pose had it when playback stops.
      entry.restTranslation = entry.translation.slice();
      entry.restRotation = entry.rotation.slice();
      entry.restScale = entry.scale.slice();
      return entry;
    });
    nodes.forEach(function (node) {
      node.children.forEach(function (child) {
        if (nodes[child]) { nodes[child].parent = node.index; }
      });
    });
    // Parents before children, so one pass computes every world matrix.
    var order = [], seen = {};
    function visit(index) {
      if (seen[index] || !nodes[index]) { return; }
      seen[index] = true;
      order.push(index);
      nodes[index].children.forEach(visit);
    }
    nodes.forEach(function (node) { if (node.parent === -1) { visit(node.index); } });
    nodes.forEach(function (node) { visit(node.index); });   // cycles, defensively
    return { nodes: nodes, order: order };
  }

  function updateWorld(graph) {
    graph.order.forEach(function (index) {
      var node = graph.nodes[index];
      if (node.matrix) {
        node.local.set(node.matrix);
      } else {
        fromTRS(node.local, node.translation, node.rotation, node.scale);
      }
      if (node.parent === -1) {
        node.world.set(node.local);
      } else {
        multiply(node.world, graph.nodes[node.parent].world, node.local);
      }
    });
  }

  // ------------------------------------------------------------- the model --

  function loadModel(buffer) {
    var parsed = parseGLB(buffer);
    var gltf = parsed.json, bin = parsed.bin;
    var graph = buildNodes(gltf);
    updateWorld(graph);

    var primitives = [];
    var skinned = 0, vertices = 0, triangles = 0;
    graph.nodes.forEach(function (node) {
      if (node.mesh === undefined) { return; }
      var mesh = (gltf.meshes || [])[node.mesh];
      if (!mesh) { return; }
      (mesh.primitives || []).forEach(function (prim) {
        if (prim.mode !== undefined && prim.mode !== 4) { return; }  // TRIANGLES
        var attributes = prim.attributes || {};
        if (attributes.POSITION === undefined) { return; }
        var position = readFloats(gltf, bin, attributes.POSITION);
        if (!position) { return; }
        var normal = attributes.NORMAL === undefined
          ? null : readFloats(gltf, bin, attributes.NORMAL);
        var indices = prim.indices === undefined
          ? null : readAccessor(gltf, bin, prim.indices);
        var entry = {
          node: node,
          name: node.name + "/" + (mesh.name || "mesh"),
          position: position.data,
          normal: normal ? normal.data : null,
          indices: indices ? indices.data : null,
          count: indices ? indices.count : position.count,
          vertices: position.count,
          skin: null
        };
        var skin = node.skin === undefined ? null : (gltf.skins || [])[node.skin];
        if (skin && attributes.JOINTS_0 !== undefined &&
            attributes.WEIGHTS_0 !== undefined) {
          var joints = readAccessor(gltf, bin, attributes.JOINTS_0);
          var weights = readFloats(gltf, bin, attributes.WEIGHTS_0);
          var inverse = skin.inverseBindMatrices === undefined
            ? null : readFloats(gltf, bin, skin.inverseBindMatrices);
          if (joints && weights) {
            entry.skin = {
              joints: joints.data,
              weights: weights.data,
              nodes: skin.joints || [],
              inverse: inverse ? inverse.data : null,
              // The buffer the skinned positions are rewritten into every
              // frame, allocated once: a per-frame allocation of a 15k-vertex
              // mesh is a garbage collector pause you can see.
              skinnedPosition: new Float32Array(position.data.length),
              skinnedNormal: normal
                ? new Float32Array(normal.data.length) : null,
              matrices: null
            };
            skinned += 1;
          }
        }
        vertices += entry.vertices;
        triangles += Math.floor(entry.count / 3);
        primitives.push(entry);
      });
    });

    var animations = (gltf.animations || []).map(function (animation, index) {
      var channels = [];
      var duration = 0;
      (animation.channels || []).forEach(function (channel) {
        var sampler = (animation.samplers || [])[channel.sampler];
        var target = channel.target || {};
        if (!sampler || target.node === undefined) { return; }
        if (["translation", "rotation", "scale"].indexOf(target.path) === -1) {
          return;   // morph weights are not something this viewer can show
        }
        var input = readFloats(gltf, bin, sampler.input);
        var output = readFloats(gltf, bin, sampler.output);
        if (!input || !output) { return; }
        duration = Math.max(duration, input.data[input.data.length - 1] || 0);
        channels.push({
          node: target.node, path: target.path,
          times: input.data, values: output.data,
          size: target.path === "rotation" ? 4 : 3,
          step: sampler.interpolation === "STEP"
        });
      });
      return { name: animation.name || ("animation " + (index + 1)),
               channels: channels, duration: duration };
    }).filter(function (animation) { return animation.channels.length > 0; });

    return {
      gltf: gltf, graph: graph, primitives: primitives,
      animations: animations, skinned: skinned,
      vertices: vertices, triangles: triangles
    };
  }

  // ------------------------------------------------------------- skinning --
  //
  // Linear blend skinning, on the CPU.  A uniform array of joint matrices is
  // the usual way and it is faster, but WebGL1's vertex-uniform budget is not
  // big enough for a Rigify deform skeleton on every machine, and a viewer
  // that silently showed a T-pose on the one character it was built for would
  // be worse than no playback at all.  15k vertices at four influences is a
  // few milliseconds a frame, which is what a preview can afford.

  function jointMatrices(model, primitive) {
    var skin = primitive.skin;
    var count = skin.nodes.length;
    if (!skin.matrices || skin.matrices.length !== count * 16) {
      skin.matrices = new Float32Array(count * 16);
    }
    var temp = new Float32Array(16), bind = new Float32Array(16);
    for (var j = 0; j < count; j++) {
      var node = model.graph.nodes[skin.nodes[j]];
      if (!node) { identity(temp); } else { temp.set(node.world); }
      if (skin.inverse) {
        bind.set(skin.inverse.subarray(j * 16, j * 16 + 16));
        multiply(skin.matrices.subarray(j * 16, j * 16 + 16), temp, bind);
      } else {
        skin.matrices.set(temp, j * 16);
      }
    }
    return skin.matrices;
  }

  function applySkin(model, primitive) {
    var skin = primitive.skin;
    var matrices = jointMatrices(model, primitive);
    var source = primitive.position, target = skin.skinnedPosition;
    var normals = primitive.normal, outNormals = skin.skinnedNormal;
    var joints = skin.joints, weights = skin.weights;
    var blended = new Float32Array(16);
    for (var v = 0; v < primitive.vertices; v++) {
      for (var k = 0; k < 16; k++) { blended[k] = 0; }
      var any = 0;
      for (var i = 0; i < 4; i++) {
        var weight = weights[v * 4 + i];
        if (!weight) { continue; }
        any += weight;
        var base = joints[v * 4 + i] * 16;
        for (var m = 0; m < 16; m++) { blended[m] += matrices[base + m] * weight; }
      }
      var x = source[v * 3], y = source[v * 3 + 1], z = source[v * 3 + 2];
      if (!any) {
        target[v * 3] = x; target[v * 3 + 1] = y; target[v * 3 + 2] = z;
        if (outNormals && normals) {
          outNormals[v * 3] = normals[v * 3];
          outNormals[v * 3 + 1] = normals[v * 3 + 1];
          outNormals[v * 3 + 2] = normals[v * 3 + 2];
        }
        continue;
      }
      target[v * 3] = blended[0] * x + blended[4] * y + blended[8] * z + blended[12];
      target[v * 3 + 1] = blended[1] * x + blended[5] * y + blended[9] * z + blended[13];
      target[v * 3 + 2] = blended[2] * x + blended[6] * y + blended[10] * z + blended[14];
      if (outNormals && normals) {
        var nx = normals[v * 3], ny = normals[v * 3 + 1], nz = normals[v * 3 + 2];
        // The blended matrix rather than its inverse transpose: a skinning
        // matrix is very nearly rigid, and a preview does not pay for the
        // difference.
        outNormals[v * 3] = blended[0] * nx + blended[4] * ny + blended[8] * nz;
        outNormals[v * 3 + 1] = blended[1] * nx + blended[5] * ny + blended[9] * nz;
        outNormals[v * 3 + 2] = blended[2] * nx + blended[6] * ny + blended[10] * nz;
      }
    }
    return target;
  }

  // ------------------------------------------------------------ animation --

  function sampleChannel(channel, time) {
    var times = channel.times, size = channel.size;
    var last = times.length - 1;
    if (last < 0) { return null; }
    if (time <= times[0]) { return channel.values.subarray(0, size); }
    if (time >= times[last]) {
      return channel.values.subarray(last * size, last * size + size);
    }
    var low = 0, high = last;
    while (high - low > 1) {
      var middle = (low + high) >> 1;
      if (times[middle] <= time) { low = middle; } else { high = middle; }
    }
    if (channel.step) {
      return channel.values.subarray(low * size, low * size + size);
    }
    var span = times[high] - times[low];
    var t = span > 0 ? (time - times[low]) / span : 0;
    var a = channel.values.subarray(low * size, low * size + size);
    var b = channel.values.subarray(high * size, high * size + size);
    var out = new Float32Array(size);
    if (size === 4) { return slerp(out, a, b, t); }
    for (var i = 0; i < size; i++) { out[i] = a[i] + (b[i] - a[i]) * t; }
    return out;
  }

  function poseAt(model, animation, time) {
    animation.channels.forEach(function (channel) {
      var node = model.graph.nodes[channel.node];
      if (!node) { return; }
      var value = sampleChannel(channel, time);
      if (!value) { return; }
      if (channel.path === "translation") {
        node.translation[0] = value[0];
        node.translation[1] = value[1];
        node.translation[2] = value[2];
      } else if (channel.path === "scale") {
        node.scale[0] = value[0]; node.scale[1] = value[1]; node.scale[2] = value[2];
      } else {
        node.rotation[0] = value[0]; node.rotation[1] = value[1];
        node.rotation[2] = value[2]; node.rotation[3] = value[3];
      }
      // A node the animation drives cannot also be using a baked matrix.
      node.matrix = null;
    });
    updateWorld(model.graph);
  }

  function restPose(model) {
    model.graph.nodes.forEach(function (node) {
      node.translation = node.restTranslation.slice();
      node.rotation = node.restRotation.slice();
      node.scale = node.restScale.slice();
    });
    updateWorld(model.graph);
  }

  // --------------------------------------------------------------- shaders --

  var VERTEX_SHADER = [
    "attribute vec3 position;",
    "attribute vec3 normal;",
    "uniform mat4 model;",
    "uniform mat4 viewProjection;",
    "varying vec3 vNormal;",
    "varying vec3 vPosition;",
    "void main() {",
    "  vec4 world = model * vec4(position, 1.0);",
    "  vPosition = world.xyz;",
    "  vNormal = mat3(model) * normal;",
    "  gl_Position = viewProjection * world;",
    "}"
  ].join("\n");

  // One clay material lit from the camera plus a cool fill, and an orange rim
  // so the silhouette reads against the page's own dark background — the same
  // accent the rest of this UI is built on.
  var FRAGMENT_SHADER = [
    "precision mediump float;",
    "varying vec3 vNormal;",
    "varying vec3 vPosition;",
    "uniform vec3 eye;",
    "void main() {",
    "  vec3 n = normalize(vNormal);",
    "  vec3 v = normalize(eye - vPosition);",
    "  if (dot(n, v) < 0.0) { n = -n; }",
    "  float key = max(dot(n, normalize(v + vec3(0.4, 0.7, 0.2))), 0.0);",
    "  float fill = max(dot(n, normalize(vec3(-0.6, -0.2, 0.5))), 0.0);",
    "  float rim = pow(1.0 - max(dot(n, v), 0.0), 3.0);",
    "  vec3 colour = vec3(0.30, 0.31, 0.33) * (0.28 + 0.72 * key);",
    "  colour += vec3(0.10, 0.12, 0.16) * fill;",
    "  colour += vec3(0.95, 0.55, 0.18) * rim * 0.55;",
    "  gl_FragColor = vec4(colour, 1.0);",
    "}"
  ].join("\n");

  function compile(gl, kind, source) {
    var shader = gl.createShader(kind);
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
      var log = gl.getShaderInfoLog(shader);
      gl.deleteShader(shader);
      throw new Error("The viewer's shader would not compile: " + log);
    }
    return shader;
  }

  // ---------------------------------------------------------------- viewer --

  function create(canvas) {
    var gl = null;
    try {
      gl = canvas.getContext("webgl", { antialias: true, alpha: true }) ||
           canvas.getContext("experimental-webgl", { antialias: true });
    } catch (e) { gl = null; }
    if (!gl) {
      return { supported: false, error: "This browser has no WebGL, so the "
                                       + "model view cannot draw." };
    }

    var program = gl.createProgram();
    gl.attachShader(program, compile(gl, gl.VERTEX_SHADER, VERTEX_SHADER));
    gl.attachShader(program, compile(gl, gl.FRAGMENT_SHADER, FRAGMENT_SHADER));
    gl.bindAttribLocation(program, 0, "position");
    gl.bindAttribLocation(program, 1, "normal");
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
      return { supported: false,
               error: "The viewer's shader would not link: "
                      + gl.getProgramInfoLog(program) };
    }
    var uniforms = {
      model: gl.getUniformLocation(program, "model"),
      viewProjection: gl.getUniformLocation(program, "viewProjection"),
      eye: gl.getUniformLocation(program, "eye")
    };

    var state = {
      model: null, buffers: [], frame: null,
      yaw: 0.7, pitch: 0.45, distance: 3, centre: [0, 0, 0], radius: 1,
      pan: [0, 0, 0], playing: false, animation: 0, time: 0, clock: 0,
      dirty: true, onFrame: null
    };
    var projection = new Float32Array(16);
    var view = new Float32Array(16);
    var viewProjection = new Float32Array(16);
    var eye = new Float32Array(3);

    function releaseBuffers() {
      state.buffers.forEach(function (entry) {
        if (entry.position) { gl.deleteBuffer(entry.position); }
        if (entry.normal) { gl.deleteBuffer(entry.normal); }
        if (entry.indices) { gl.deleteBuffer(entry.indices); }
      });
      state.buffers = [];
    }

    function upload(model) {
      releaseBuffers();
      model.primitives.forEach(function (primitive) {
        var entry = { primitive: primitive };
        entry.position = gl.createBuffer();
        gl.bindBuffer(gl.ARRAY_BUFFER, entry.position);
        gl.bufferData(gl.ARRAY_BUFFER, primitive.position,
                      primitive.skin ? gl.DYNAMIC_DRAW : gl.STATIC_DRAW);
        if (primitive.normal) {
          entry.normal = gl.createBuffer();
          gl.bindBuffer(gl.ARRAY_BUFFER, entry.normal);
          gl.bufferData(gl.ARRAY_BUFFER, primitive.normal,
                        primitive.skin ? gl.DYNAMIC_DRAW : gl.STATIC_DRAW);
        }
        if (primitive.indices) {
          entry.indices = gl.createBuffer();
          entry.indexType = primitive.indices instanceof Uint32Array
            ? gl.UNSIGNED_INT : gl.UNSIGNED_SHORT;
          if (primitive.indices instanceof Uint32Array &&
              !gl.getExtension("OES_element_index_uint")) {
            // No 32-bit indices on this machine: narrow them, which is safe
            // for anything a preview is ever asked to draw.
            var narrowed = new Uint16Array(primitive.indices.length);
            narrowed.set(primitive.indices);
            entry.indexType = gl.UNSIGNED_SHORT;
            gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, entry.indices);
            gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, narrowed, gl.STATIC_DRAW);
          } else {
            gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, entry.indices);
            gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, primitive.indices,
                          gl.STATIC_DRAW);
          }
        }
        state.buffers.push(entry);
      });
    }

    /** The bounding sphere of the whole model, in world space. */
    function measure(model) {
      var min = [Infinity, Infinity, Infinity];
      var max = [-Infinity, -Infinity, -Infinity];
      model.primitives.forEach(function (primitive) {
        var world = primitive.skin ? null : primitive.node.world;
        var data = primitive.position;
        for (var v = 0; v < primitive.vertices; v++) {
          var x = data[v * 3], y = data[v * 3 + 1], z = data[v * 3 + 2];
          if (world) {
            var wx = world[0] * x + world[4] * y + world[8] * z + world[12];
            var wy = world[1] * x + world[5] * y + world[9] * z + world[13];
            var wz = world[2] * x + world[6] * y + world[10] * z + world[14];
            x = wx; y = wy; z = wz;
          }
          if (x < min[0]) { min[0] = x; } if (x > max[0]) { max[0] = x; }
          if (y < min[1]) { min[1] = y; } if (y > max[1]) { max[1] = y; }
          if (z < min[2]) { min[2] = z; } if (z > max[2]) { max[2] = z; }
        }
      });
      if (!isFinite(min[0])) { return { centre: [0, 0, 0], radius: 1 }; }
      var centre = [(min[0] + max[0]) / 2, (min[1] + max[1]) / 2,
                    (min[2] + max[2]) / 2];
      var radius = Math.max(
        Math.hypot(max[0] - centre[0], max[1] - centre[1], max[2] - centre[2]),
        1e-3);
      return { centre: centre, radius: radius };
    }

    function resize() {
      var ratio = Math.min(global.devicePixelRatio || 1, 2);
      var width = Math.max(1, Math.round(canvas.clientWidth * ratio));
      var height = Math.max(1, Math.round(canvas.clientHeight * ratio));
      if (canvas.width !== width || canvas.height !== height) {
        canvas.width = width;
        canvas.height = height;
        state.dirty = true;
      }
    }

    function draw() {
      resize();
      gl.viewport(0, 0, canvas.width, canvas.height);
      gl.clearColor(0, 0, 0, 0);
      gl.enable(gl.DEPTH_TEST);
      gl.enable(gl.CULL_FACE);
      gl.cullFace(gl.BACK);
      gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
      if (!state.model) { return; }

      var aspect = canvas.width / Math.max(1, canvas.height);
      perspective(projection, 0.9, aspect, state.radius * 0.01,
                  state.radius * 100);
      var cp = Math.cos(state.pitch), sp = Math.sin(state.pitch);
      eye[0] = state.centre[0] + state.pan[0] +
               state.distance * cp * Math.sin(state.yaw);
      eye[1] = state.centre[1] + state.pan[1] + state.distance * sp;
      eye[2] = state.centre[2] + state.pan[2] +
               state.distance * cp * Math.cos(state.yaw);
      lookAt(view, eye,
             [state.centre[0] + state.pan[0], state.centre[1] + state.pan[1],
              state.centre[2] + state.pan[2]], [0, 1, 0]);
      multiply(viewProjection, projection, view);

      gl.useProgram(program);
      gl.uniformMatrix4fv(uniforms.viewProjection, false, viewProjection);
      gl.uniform3f(uniforms.eye, eye[0], eye[1], eye[2]);
      gl.enableVertexAttribArray(0);
      gl.enableVertexAttribArray(1);

      state.buffers.forEach(function (entry) {
        var primitive = entry.primitive;
        if (primitive.skin) {
          // A skinned mesh's vertices are already in skin space, so the node
          // transform is not applied to it — glTF says so explicitly, and it
          // is why a rigged character drawn with its node matrix lands in the
          // wrong place.
          gl.uniformMatrix4fv(uniforms.model, false, identity(new Float32Array(16)));
          var skinned = applySkin(state.model, primitive);
          gl.bindBuffer(gl.ARRAY_BUFFER, entry.position);
          gl.bufferSubData(gl.ARRAY_BUFFER, 0, skinned);
          if (entry.normal && primitive.skin.skinnedNormal) {
            gl.bindBuffer(gl.ARRAY_BUFFER, entry.normal);
            gl.bufferSubData(gl.ARRAY_BUFFER, 0, primitive.skin.skinnedNormal);
          }
        } else {
          gl.uniformMatrix4fv(uniforms.model, false, primitive.node.world);
        }
        gl.bindBuffer(gl.ARRAY_BUFFER, entry.position);
        gl.vertexAttribPointer(0, 3, gl.FLOAT, false, 0, 0);
        if (entry.normal) {
          gl.bindBuffer(gl.ARRAY_BUFFER, entry.normal);
          gl.vertexAttribPointer(1, 3, gl.FLOAT, false, 0, 0);
        } else {
          gl.disableVertexAttribArray(1);
          gl.vertexAttrib3f(1, 0, 0, 1);
        }
        if (entry.indices) {
          gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, entry.indices);
          gl.drawElements(gl.TRIANGLES, primitive.count, entry.indexType, 0);
        } else {
          gl.drawArrays(gl.TRIANGLES, 0, primitive.count);
        }
        if (!entry.normal) { gl.enableVertexAttribArray(1); }
      });
      state.dirty = false;
    }

    function tick(now) {
      state.frame = global.requestAnimationFrame(tick);
      if (state.playing && state.model && state.model.animations.length) {
        var animation = state.model.animations[state.animation];
        var elapsed = state.clock ? (now - state.clock) / 1000 : 0;
        state.clock = now;
        if (animation.duration > 0) {
          state.time = (state.time + elapsed) % animation.duration;
        }
        poseAt(state.model, animation, state.time);
        state.dirty = true;
        if (state.onFrame) { state.onFrame(state.time, animation); }
      }
      if (state.dirty || canvas.clientWidth !== canvas._lastWidth) {
        canvas._lastWidth = canvas.clientWidth;
        draw();
      }
    }

    // -- input: orbit with the left button, pan with shift, zoom on wheel --
    var dragging = null;
    canvas.addEventListener("pointerdown", function (event) {
      dragging = { x: event.clientX, y: event.clientY, pan: event.shiftKey };
      try { canvas.setPointerCapture(event.pointerId); } catch (e) { /* older */ }
    });
    canvas.addEventListener("pointermove", function (event) {
      if (!dragging) { return; }
      var dx = event.clientX - dragging.x, dy = event.clientY - dragging.y;
      dragging.x = event.clientX; dragging.y = event.clientY;
      if (dragging.pan) {
        var scale = state.distance / Math.max(1, canvas.clientHeight);
        state.pan[0] -= dx * scale * Math.cos(state.yaw);
        state.pan[2] += dx * scale * Math.sin(state.yaw);
        state.pan[1] += dy * scale;
      } else {
        state.yaw -= dx * 0.008;
        state.pitch = Math.max(-1.5, Math.min(1.5, state.pitch + dy * 0.008));
      }
      state.dirty = true;
    });
    function endDrag() { dragging = null; }
    canvas.addEventListener("pointerup", endDrag);
    canvas.addEventListener("pointercancel", endDrag);
    canvas.addEventListener("pointerleave", endDrag);
    canvas.addEventListener("wheel", function (event) {
      event.preventDefault();
      var factor = Math.exp((event.deltaY > 0 ? 1 : -1) * 0.12);
      state.distance = Math.max(state.radius * 0.15,
                                Math.min(state.radius * 40,
                                         state.distance * factor));
      state.dirty = true;
    }, { passive: false });

    state.frame = global.requestAnimationFrame(tick);

    return {
      supported: true,

      /** Draw one .glb, given as an ArrayBuffer. Returns what is in it. */
      show: function (buffer) {
        var model = loadModel(buffer);
        state.model = model;
        state.playing = false;
        state.time = 0;
        state.animation = 0;
        restPose(model);
        upload(model);
        var bounds = measure(model);
        state.centre = bounds.centre;
        state.radius = bounds.radius;
        state.distance = bounds.radius * 2.8;
        state.pan = [0, 0, 0];
        state.yaw = 0.7;
        state.pitch = 0.35;
        state.dirty = true;
        return {
          objects: model.primitives.length,
          vertices: model.vertices,
          triangles: model.triangles,
          skinned: model.skinned,
          animations: model.animations.map(function (a) {
            return { name: a.name, duration: a.duration };
          })
        };
      },

      play: function (index) {
        if (!state.model || !state.model.animations.length) { return false; }
        state.animation = Math.max(0, Math.min(
          state.model.animations.length - 1, index || 0));
        state.playing = true;
        state.clock = 0;
        return true;
      },

      pause: function () {
        state.playing = false;
        state.clock = 0;
      },

      stop: function () {
        state.playing = false;
        state.clock = 0;
        state.time = 0;
        if (state.model) { restPose(state.model); state.dirty = true; }
      },

      playing: function () { return state.playing; },

      recentre: function () {
        if (!state.model) { return; }
        state.yaw = 0.7;
        state.pitch = 0.35;
        state.pan = [0, 0, 0];
        state.distance = state.radius * 2.8;
        state.dirty = true;
      },

      clear: function () {
        state.playing = false;
        state.model = null;
        releaseBuffers();
        state.dirty = true;
      },

      onFrame: function (fn) { state.onFrame = fn; },

      dispose: function () {
        if (state.frame) { global.cancelAnimationFrame(state.frame); }
        releaseBuffers();
        state.model = null;
      }
    };
  }

  global.ForgeGLB = {
    create: create,
    // Exported so they can be exercised without a canvas — the parser is the
    // half of this file that can be wrong in a way nobody sees.
    parseGLB: parseGLB,
    loadModel: loadModel,
    readAccessor: readAccessor,
    sampleChannel: sampleChannel
  };
}(window));
