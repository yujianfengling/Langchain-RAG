/** PDF.js 延迟加载：只有真正打开 PDF 时才下载渲染器与 worker。 */
let pdfjsPromise: Promise<any> | null = null;

export const getPdfJs = async (): Promise<any> => {
  if (!pdfjsPromise) {
    pdfjsPromise = (async () => {
      const pdfjs = await import('pdfjs-dist');
      // Vite 会把 worker 打成独立资源，不进入首屏包
      const worker = await import('pdfjs-dist/build/pdf.worker.min.mjs?url');
      pdfjs.GlobalWorkerOptions.workerSrc = worker.default;
      return pdfjs;
    })();
  }
  return pdfjsPromise;
};

export const openPdf = async (data: ArrayBuffer): Promise<any> => {
  const pdfjs = await getPdfJs();
  // PDF.js transfers its buffer to a worker; retain the caller's reusable bytes.
  return pdfjs.getDocument({ data: new Uint8Array(data.slice(0)) }).promise;
};
