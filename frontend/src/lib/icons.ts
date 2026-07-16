/**
 * Vehicle dart icon, rendered once to a canvas atlas for deck.gl's IconLayer.
 *
 * The comp's oriented marker path is M5 0 L-3.6 -3.4 L-1.8 0 L-3.6 3.4 Z
 * (pointing along +x). It is drawn rotated to point *up* so the layer's
 * getAngle can be the negated compass bearing (deck rotates
 * counterclockwise, bearings run clockwise). Drawn white with mask:true so
 * getColor applies the delay ramp tint.
 */

export interface DartAtlas {
  /** data: URL of the atlas image (IconLayer's iconAtlas accepts a string). */
  url: string
  mapping: {
    dart: {
      x: number
      y: number
      width: number
      height: number
      anchorX: number
      anchorY: number
      mask: boolean
    }
  }
}

const ATLAS_SIZE = 64
const PATH_SCALE = 6

export function buildDartAtlas(): DartAtlas {
  const canvas = document.createElement('canvas')
  canvas.width = ATLAS_SIZE
  canvas.height = ATLAS_SIZE
  const ctx = canvas.getContext('2d')
  if (ctx === null) throw new Error('2d canvas context unavailable')
  ctx.translate(ATLAS_SIZE / 2, ATLAS_SIZE / 2)
  ctx.rotate(-Math.PI / 2) // +x -> up
  ctx.scale(PATH_SCALE, PATH_SCALE)
  ctx.beginPath()
  ctx.moveTo(5, 0)
  ctx.lineTo(-3.6, -3.4)
  ctx.lineTo(-1.8, 0)
  ctx.lineTo(-3.6, 3.4)
  ctx.closePath()
  ctx.fillStyle = '#fff'
  ctx.fill()
  return {
    url: canvas.toDataURL(),
    mapping: {
      dart: {
        x: 0,
        y: 0,
        width: ATLAS_SIZE,
        height: ATLAS_SIZE,
        anchorX: ATLAS_SIZE / 2,
        anchorY: ATLAS_SIZE / 2,
        mask: true,
      },
    },
  }
}
