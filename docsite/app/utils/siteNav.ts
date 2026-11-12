// The marketing site's links, shared by its header, phone menu and footer.
export const REPO = 'https://github.com/itsOmidKarami/kraft'
export const INSTALL = '/get-started/install'

// The four pages under Product. The lines were approved with the menu board as
// a whole, not one by one: a change to them goes back to the design's owner.
export const PRODUCT_PAGES = [
  { to: '/how-it-works', name: 'How it works', line: 'The chain, the gates and the limits that keep a run in bounds.' },
  { to: '/tour', name: 'Product tour', line: 'The board, the review page, your phone and your editor.' },
  { to: '/customize', name: 'Make it yours', line: 'Your own chains, add-ons and agents.' },
  { to: '/scale', name: 'Scale up', line: 'Slices for large plans, and steps on other machines.' },
]

// The dashboard demo is a folder of static files beside the pages, not a
// route: a plain link, with the base put on by hand.
export const demoHref = (baseURL: string) => `${baseURL.replace(/\/$/, '')}/demo/`
