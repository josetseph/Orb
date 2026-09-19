import { useEffect, useRef, useState } from "react";
import {
  fetchMediaObjectUrl,
  resolveFileUrl,
  youtubeEmbedUrl,
  vimeoEmbedUrl,
} from "@/lib/utils";

/** Video/audio/YouTube preview with reliable local playback. */
export function BlobMediaPlayer({
  url,
  kbId = "default",
  kind,
  className,
}: {
  url: string;
  kbId?: string;
  kind: "video" | "audio";
  className?: string;
}) {
  const yt = youtubeEmbedUrl(url);
  const vimeo = vimeoEmbedUrl(url);
  const directUrl = !yt && !vimeo ? resolveFileUrl(url, kbId) : null;

  const [src, setSrc] = useState<string | null>(directUrl);
  const [error, setError] = useState<string | null>(null);

  const [prevUrl, setPrevUrl] = useState(url);
  if (prevUrl !== url) {
    setPrevUrl(url);
    setSrc(directUrl);
    setError(null);
  }

  const objectUrlRef = useRef<string | null>(null);
  const blobTriedRef = useRef(false);
  const disposedRef = useRef(false);

  useEffect(() => {
    disposedRef.current = false;
    blobTriedRef.current = false;
    return () => {
      disposedRef.current = true;
      if (objectUrlRef.current) {
        URL.revokeObjectURL(objectUrlRef.current);
        objectUrlRef.current = null;
      }
    };
  }, [url, kbId]);

  const onMediaError = () => {
    if (blobTriedRef.current || !src || src.startsWith("blob:")) {
      setError(
        kind === "video"
          ? "Could not play this video."
          : "Could not play this audio.",
      );
      return;
    }
    blobTriedRef.current = true;
    void fetchMediaObjectUrl(url, kbId)
      .then((blobUrl) => {
        if (disposedRef.current) {
          // Resolved after unmount/url change — revoke now or the blob leaks.
          if (blobUrl.startsWith("blob:")) URL.revokeObjectURL(blobUrl);
          return;
        }
        if (blobUrl.startsWith("blob:")) {
          if (objectUrlRef.current) URL.revokeObjectURL(objectUrlRef.current);
          objectUrlRef.current = blobUrl;
        }
        setSrc(blobUrl);
      })
      .catch(() => {
        if (!disposedRef.current) {
          setError(
            kind === "video"
              ? "Could not play this video."
              : "Could not play this audio.",
          );
        }
      });
  };

  if (yt || vimeo) {
    return (
      <iframe
        src={yt || vimeo || ""}
        title="Embedded video"
        allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
        allowFullScreen
        className={
          className ||
          "mx-auto aspect-video min-h-[200px] w-full max-w-full rounded-md bg-bg-deep shadow-sm"
        }
      />
    );
  }

  if (error) {
    return <p className="text-[12.5px] text-danger-text">{error}</p>;
  }
  if (!src) {
    return <p className="text-[12.5px] text-n-500">Loading media…</p>;
  }

  if (kind === "video") {
    return (
      <video
        controls
        playsInline
        src={src}
        className={
          className ||
          "mx-auto max-h-[70vh] w-full max-w-full rounded-md bg-bg-deep shadow-sm"
        }
        onError={onMediaError}
      >
        Your browser does not support video playback.
      </video>
    );
  }

  return (
    <audio
      controls
      src={src}
      className={className || "w-full max-w-2xl rounded-md"}
      onError={onMediaError}
    />
  );
}
