/** Lightweight WebGL probe — kept out of the three.js chunk so pages can choose a fallback before loading 3D. */
export function hasWebGL(): boolean {
  try {
    const canvas = document.createElement('canvas');
    return Boolean(canvas.getContext('webgl2') ?? canvas.getContext('webgl'));
  } catch {
    return false;
  }
}
