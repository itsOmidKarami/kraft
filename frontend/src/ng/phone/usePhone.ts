import { useEffect, useState } from "react";

export const PHONE_QUERY = "(max-width: 767px)";

/** Whether the window is phone width, live: crossing 767 swaps the shape and keeps the URL (W17 brief A.1). */
export function usePhone(): boolean {
  const [phone, setPhone] = useState(() => matchMedia(PHONE_QUERY).matches);
  useEffect(() => {
    const mq = matchMedia(PHONE_QUERY);
    const on = () => setPhone(mq.matches);
    on();
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return phone;
}
