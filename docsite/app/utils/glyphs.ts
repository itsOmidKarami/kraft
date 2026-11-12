// The marketing pages' only icons (docsite/DESIGN.md): the agent's glyph and
// Kraft's, as 24-unit stroke paths. The gate diamond is drawn in CSS.
export type Glyph = 'agent' | 'kraft'

export const GLYPHS: Record<Glyph, string[]> = {
  agent: ['M12 3l2.1 6.9L21 12l-6.9 2.1L12 21l-2.1-6.9L3 12l6.9-2.1z'],
  kraft: ['M6 8l4 4-4 4', 'M13 16h5'],
}
