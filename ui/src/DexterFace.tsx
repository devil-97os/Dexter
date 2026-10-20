import { Canvas, useFrame, useLoader, useThree } from '@react-three/fiber'
import { Component, Suspense, useEffect, useMemo, useRef, type ErrorInfo, type ReactNode, type RefObject } from 'react'
import * as THREE from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import { KTX2Loader } from 'three/examples/jsm/loaders/KTX2Loader.js'
import { MeshoptDecoder } from 'three/examples/jsm/libs/meshopt_decoder.module.js'

export type FaceState = 'idle' | 'listening' | 'thinking' | 'speaking' | 'success' | 'error'

const MODEL_URL = '/models/facecap.glb'
const MOOD_COLORS: Record<FaceState, THREE.Color> = {
  idle: new THREE.Color('#6bd9ed'), listening: new THREE.Color('#7be9f5'),
  thinking: new THREE.Color('#a9a0f4'), speaking: new THREE.Color('#6bd9ed'),
  success: new THREE.Color('#73dfb8'), error: new THREE.Color('#d78c9b'),
}

function createIrisTexture() {
  const canvas = document.createElement('canvas')
  canvas.width = 256
  canvas.height = 256
  const context = canvas.getContext('2d')
  if (!context) throw new Error('Canvas textures are unavailable')
  const center = 128
  const gradient = context.createRadialGradient(center, center, 19, center, center, 120)
  gradient.addColorStop(0, '#090b0c')
  gradient.addColorStop(0.16, '#111617')
  gradient.addColorStop(0.3, '#30393b')
  gradient.addColorStop(0.52, '#626d70')
  gradient.addColorStop(0.73, '#697477')
  gradient.addColorStop(0.9, '#454f52')
  gradient.addColorStop(1, '#242c30')
  context.fillStyle = gradient
  context.beginPath()
  context.arc(center, center, 124, 0, Math.PI * 2)
  context.fill()
  let seed = 913
  const random = () => { seed = (seed * 16807) % 2147483647; return seed / 2147483647 }
  context.save()
  context.beginPath()
  context.arc(center, center, 117, 0, Math.PI * 2)
  context.clip()
  for (let index = 0; index < 220; index += 1) {
    const angle = (index / 220) * Math.PI * 2
    const inner = 39 + random() * 17
    const outer = 92 + random() * 22
    const light = random() > 0.58
    context.strokeStyle = light ? `rgba(190, 201, 185, ${0.1 + random() * 0.19})` : `rgba(11, 20, 22, ${0.08 + random() * 0.15})`
    context.lineWidth = 0.8 + random() * 1.2
    context.beginPath()
    context.moveTo(center + Math.cos(angle) * inner, center + Math.sin(angle) * inner)
    context.lineTo(center + Math.cos(angle) * outer, center + Math.sin(angle) * outer)
    context.stroke()
  }
  context.restore()
  context.strokeStyle = 'rgba(14, 19, 18, 0.58)'
  context.lineWidth = 2
  context.beginPath()
  context.arc(center, center, 120, 0, Math.PI * 2)
  context.stroke()
  context.fillStyle = '#080a0b'
  context.beginPath()
  context.arc(center, center, 24, 0, Math.PI * 2)
  context.fill()
  const texture = new THREE.CanvasTexture(canvas)
  texture.colorSpace = THREE.SRGBColorSpace
  texture.anisotropy = 4
  return texture
}

function EyeLens({ x, texture, lensRef }: { x: number; texture: THREE.CanvasTexture; lensRef: RefObject<THREE.Group | null> }) {
  return <group ref={lensRef} position={[x, -0.022, 1.08]}>
    <mesh position={[0, 0, 0.004]} scale={[1, 1, 0.16]}>
      <sphereGeometry args={[0.023, 32, 24]} />
      <meshPhysicalMaterial map={texture} metalness={0.015} roughness={0.34} clearcoat={0.38} clearcoatRoughness={0.2} />
    </mesh>
    <mesh position={[-0.006, 0.007, 0.009]}>
      <circleGeometry args={[0.0018, 16]} />
      <meshBasicMaterial color="#dbe7e7" transparent opacity={0.72} />
    </mesh>
  </group>
}

function Android({ state }: { state: FaceState }) {
  const { gl } = useThree()
  const root = useRef<THREE.Group>(null)
  const faceMesh = useRef<THREE.Mesh>(null)
  const leftEye = useRef<THREE.Group>(null)
  const rightEye = useRef<THREE.Group>(null)
  const leftLens = useRef<THREE.Group>(null)
  const rightLens = useRef<THREE.Group>(null)
  const startedAt = useRef<number | null>(null)
  const nextBlinkAt = useRef(3.5)
  const blinkAmount = useRef(0)
  const reducedMotion = useRef(window.matchMedia('(prefers-reduced-motion: reduce)').matches)
  const disposalTimer = useRef<number | null>(null)
  const irisTexture = useMemo(createIrisTexture, [])
  const ktx2 = useMemo(() => new KTX2Loader().setTranscoderPath('/models/basis/').detectSupport(gl), [gl])
  const gltf = useLoader(GLTFLoader, MODEL_URL, (loader) => {
    loader.setKTX2Loader(ktx2)
    loader.setMeshoptDecoder(MeshoptDecoder)
  })
  const model = useMemo(() => {
    const clone = gltf.scene.clone(true)
    const bounds = new THREE.Box3().setFromObject(clone)
    const size = bounds.getSize(new THREE.Vector3())
    const center = bounds.getCenter(new THREE.Vector3())
    clone.position.sub(center)
    clone.scale.setScalar(1.9 / Math.max(size.y, 0.001))
    const leftEyeNode = clone.getObjectByName('grp_eyeLeft')
    const rightEyeNode = clone.getObjectByName('grp_eyeRight')
    leftEye.current = leftEyeNode instanceof THREE.Group ? leftEyeNode : null
    rightEye.current = rightEyeNode instanceof THREE.Group ? rightEyeNode : null
    clone.traverse((object) => {
      if (!(object instanceof THREE.Mesh)) return
      object.frustumCulled = true
      if (object.name === 'mesh_2' && object.morphTargetDictionary) faceMesh.current = object
      const eye = object.name === 'mesh_0' || object.name === 'mesh_1'
      const teeth = object.name === 'mesh_3'
      const sources = Array.isArray(object.material) ? object.material : [object.material]
      const materials = sources.map((source) => {
        const material = source.clone()
        material.color.set(eye ? '#d4dcdf' : teeth ? '#aeb6bc' : '#858b93')
        if ('metalness' in material) material.metalness = eye ? 0.12 : teeth ? 0.32 : 0.44
        if ('roughness' in material) material.roughness = eye ? 0.24 : teeth ? 0.34 : 0.5
        if ('emissive' in material) material.emissive.set(0x080e14)
        if ('clearcoat' in material) material.clearcoat = eye ? 0.35 : 0.08
        material.needsUpdate = true
        return material
      })
      object.material = Array.isArray(object.material) ? materials : materials[0]
    })
    return clone
  }, [gltf.scene])
  useEffect(() => {
    // Defer disposal by one tick so React StrictMode's development-only
    // cleanup/re-setup cycle does not dispose resources still in use.
    if (disposalTimer.current !== null) window.clearTimeout(disposalTimer.current)
    disposalTimer.current = null
    return () => {
      disposalTimer.current = window.setTimeout(() => {
        model.traverse((object) => {
          if (!(object instanceof THREE.Mesh)) return
          const materials = Array.isArray(object.material) ? object.material : [object.material]
          materials.forEach((material) => material.dispose())
        })
        ktx2.dispose()
        irisTexture.dispose()
        disposalTimer.current = null
      }, 0)
    }
  }, [irisTexture, ktx2, model])

  useFrame(({ clock }, delta) => {
    const time = clock.elapsedTime
    if (startedAt.current === null) startedAt.current = time
    const elapsed = time - startedAt.current
    const limitMotion = reducedMotion.current || document.documentElement.dataset.reducedMotion === 'true'
    const active = state === 'listening' || state === 'thinking' || state === 'speaking'
    if (!limitMotion && elapsed >= nextBlinkAt.current) {
      blinkAmount.current = 1
      nextBlinkAt.current = elapsed + (active ? 4.4 : 3.8) + Math.random() * 2.8
    }
    blinkAmount.current = Math.max(0, blinkAmount.current - delta * 9.5)
    const lidScale = Math.max(0.035, 1 - blinkAmount.current)
    if (leftLens.current) leftLens.current.scale.y = THREE.MathUtils.damp(leftLens.current.scale.y, lidScale, 20, delta)
    if (rightLens.current) rightLens.current.scale.y = THREE.MathUtils.damp(rightLens.current.scale.y, lidScale, 20, delta)

    if (root.current && !limitMotion) {
      const turn = state === 'thinking' ? Math.sin(time * 0.31) * 0.035 : state === 'listening' ? 0 : Math.sin(time * 0.17) * 0.012
      const tilt = state === 'thinking' ? -0.014 : state === 'listening' ? 0.008 : 0
      root.current.rotation.y = THREE.MathUtils.damp(root.current.rotation.y, turn, 1.3, delta)
      root.current.rotation.x = THREE.MathUtils.damp(root.current.rotation.x, tilt, 1.4, delta)
      root.current.position.y = THREE.MathUtils.damp(root.current.position.y, 0.004 * Math.sin(time * 0.75), 1.5, delta)
    }
    if (leftEye.current && rightEye.current && !limitMotion) {
      const target = state === 'thinking' ? -0.035 : state === 'listening' ? 0 : Math.sin(time * 0.19) * 0.012
      leftEye.current.rotation.y = THREE.MathUtils.damp(leftEye.current.rotation.y, target, 1.5, delta)
      rightEye.current.rotation.y = THREE.MathUtils.damp(rightEye.current.rotation.y, -target, 1.5, delta)
      const lensShift = state === 'thinking' ? 0.0014 : state === 'listening' ? 0 : Math.sin(time * 0.19) * 0.0007
      if (leftLens.current) leftLens.current.position.x = THREE.MathUtils.damp(leftLens.current.position.x, -0.16 + lensShift, 2, delta)
      if (rightLens.current) rightLens.current.position.x = THREE.MathUtils.damp(rightLens.current.position.x, 0.16 - lensShift, 2, delta)
    }

    const face = faceMesh.current
    if (!face?.morphTargetDictionary || !face.morphTargetInfluences) return
    const dictionary = face.morphTargetDictionary
    const influences = face.morphTargetInfluences
    const desired: Record<string, number> = {
      eyeBlink_L: blinkAmount.current,
      eyeBlink_R: Math.max(0, blinkAmount.current - 0.035),
      browInnerUp: state === 'listening' ? 0.055 : state === 'thinking' ? 0.075 : 0,
      browDown_L: state === 'error' ? 0.12 : 0,
      browDown_R: state === 'error' ? 0.12 : 0,
      mouthSmile_L: state === 'success' ? 0.12 : 0,
      mouthSmile_R: state === 'success' ? 0.12 : 0,
      mouthFrown_L: state === 'error' ? 0.1 : 0,
      mouthFrown_R: state === 'error' ? 0.1 : 0,
      jawOpen: state === 'speaking' && !limitMotion ? 0.08 + (0.17 * (0.5 + 0.5 * Math.sin(time * 7.4))) : 0,
      mouthFunnel: state === 'speaking' && !limitMotion ? 0.035 + (0.06 * (0.5 + 0.5 * Math.sin(time * 5.1 + 0.8))) : 0,
      eyeLookUp_L: state === 'thinking' ? 0.1 : 0,
      eyeLookUp_R: state === 'thinking' ? 0.1 : 0,
    }
    for (const [name, index] of Object.entries(dictionary)) {
      const goal = desired[name] ?? 0
      influences[index] = THREE.MathUtils.damp(influences[index], goal, 10, delta)
    }
  })

  return <group ref={root} position={[0, 0.015, 0]}>
    <primitive object={model} dispose={null} />
    <EyeLens x={-0.16} texture={irisTexture} lensRef={leftLens} />
    <EyeLens x={0.16} texture={irisTexture} lensRef={rightLens} />
    <pointLight position={[0, 0.18, 0.85]} color={MOOD_COLORS[state]} intensity={state === 'thinking' ? 0.35 : 0.2} distance={2.4} />
  </group>
}

type BoundaryProps = { children: ReactNode }
type BoundaryState = { error: string | null }
class FaceErrorBoundary extends Component<BoundaryProps, BoundaryState> {
  state: BoundaryState = { error: null }
  static getDerivedStateFromError(error: Error): BoundaryState { return { error: error.message } }
  componentDidCatch(error: Error, _info: ErrorInfo) { console.error('Dexter face failed to load:', error.message) }
  render() { return this.state.error ? <div className="face-fallback"><span>Dexter's 3D face is unavailable.</span></div> : this.props.children }
}

export default function DexterFace({ state = 'idle', compact = false }: { state?: FaceState; compact?: boolean }) {
  const label = state === 'idle' ? 'Ready' : state[0].toUpperCase() + state.slice(1)
  return <div className={`face-stage ${compact ? 'compact' : ''}`}>
    <div className={`face-aura aura-${state}`} />
    <FaceErrorBoundary>
      <Canvas camera={{ position: [0, 0.12, 3.2], fov: 34 }} dpr={[1, 1.5]} gl={{ antialias: true, alpha: true, powerPreference: 'low-power' }} fallback={<div className="face-fallback"><span>WebGL is unavailable</span></div>}>
        <color attach="background" args={['#080e17']} />
        <ambientLight intensity={0.72} />
        <directionalLight position={[-2.5, 3, 4]} intensity={1.45} color="#e6f3ff" />
        <directionalLight position={[2.8, 0.4, -2]} intensity={0.85} color="#617cb4" />
        <pointLight position={[-1.2, -0.6, 2]} intensity={0.26} color="#40bad5" distance={4} />
        <Suspense fallback={null}><Android state={state} /></Suspense>
      </Canvas>
    </FaceErrorBoundary>
    {!compact && <div className="face-state"><i className={`status-dot ${state}`} />{label}</div>}
  </div>
}
