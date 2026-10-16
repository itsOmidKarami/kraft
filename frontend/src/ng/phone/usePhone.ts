import { useMedia } from "../useMedia";

export const PHONE_QUERY = "(max-width: 767px)";

/** Whether the window is phone width, live: crossing 767 swaps the shape and keeps the URL (W17 brief A.1). */
export const usePhone = (): boolean => useMedia(PHONE_QUERY);
