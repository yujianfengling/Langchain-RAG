"""录屏鼠标淡黄光圈：常亮跟随鼠标，点击时扩散。纯标准库，python scripts/mouse_halo.py 运行。"""
import ctypes
import ctypes.wintypes as wt
import math
import time

COLOR = (255, 229, 143)  # 淡黄 #FFE58F
RADIUS = 34              # 光圈半径（物理像素）
THICKNESS = 5            # 圆环粗细
OPACITY = 170            # 圆环不透明度 0-255
FILL = 14                # 环内极淡填充，0 = 纯圆环
RIPPLE_SECONDS = 0.38    # 点击扩散动画时长
RIPPLE_EXPAND = 1.7      # 扩散终点半径 = RADIUS * (1 + RIPPLE_EXPAND)
CLASS_NAME = "MouseHaloOverlay"

WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
ULW_ALPHA = 2
SW_HIDE, SW_SHOWNA = 0, 8
WM_HOTKEY = 0x0312
PM_REMOVE = 1
MOD_CONTROL, MOD_ALT = 2, 1
VK_LBUTTON, VK_RBUTTON = 1, 2

_MAX_R = RADIUS * (1 + RIPPLE_EXPAND) + THICKNESS + 2
SIZE = math.ceil(_MAX_R) * 2 + 2
CENTER = SIZE // 2

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32
winmm = ctypes.windll.winmm

# 64 位下默认 restype=c_int 会截断句柄、默认参数转换会把 WS_POPUP 当有符号 int 溢出，
# 所以凡涉及句柄/DWORD 风格的调用都必须显式声明原型
user32.GetDC.restype = wt.HDC
user32.GetDC.argtypes = [wt.HWND]
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
user32.CreateWindowExW.restype = wt.HWND
user32.CreateWindowExW.argtypes = [
    wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.DestroyWindow.argtypes = [wt.HWND]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.GetAsyncKeyState.restype = ctypes.c_short
gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateDIBSection.restype = wt.HBITMAP
gdi32.CreateDIBSection.argtypes = [
    wt.HDC, ctypes.c_void_p, wt.UINT, ctypes.POINTER(ctypes.c_void_p), wt.HANDLE, wt.DWORD]
gdi32.SelectObject.restype = wt.HGDIOBJ
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wt.HDC]
kernel32.GetModuleHandleW.restype = wt.HMODULE
kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
kernel32.GetCurrentProcess.restype = wt.HANDLE
kernel32.SetPriorityClass.argtypes = [wt.HANDLE, wt.DWORD]
kernel32.SetProcessInformation.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD]
kernel32.CreateWaitableTimerExW.restype = wt.HANDLE
kernel32.CreateWaitableTimerExW.argtypes = [wt.LPVOID, wt.LPCWSTR, wt.DWORD, wt.DWORD]
kernel32.SetWaitableTimer.argtypes = [
    wt.HANDLE, ctypes.POINTER(ctypes.c_longlong), wt.LONG, wt.LPVOID, wt.LPVOID, wt.BOOL]
kernel32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
kernel32.CloseHandle.argtypes = [wt.HANDLE]

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wt.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH),
                ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
                ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


class POWER_THROTTLING_STATE(ctypes.Structure):
    _fields_ = [("Version", wt.DWORD), ("ControlMask", wt.DWORD), ("StateMask", wt.DWORD)]


user32.UpdateLayeredWindow.argtypes = [
    wt.HWND, wt.HDC, ctypes.POINTER(wt.POINT), ctypes.POINTER(wt.SIZE),
    wt.HDC, ctypes.POINTER(wt.POINT), wt.DWORD, ctypes.POINTER(BLENDFUNCTION), wt.DWORD]
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.PeekMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT, wt.UINT]
user32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]


def set_dpi_aware():
    # 不声明 DPI 感知时，缩放显示器上 GetCursorPos 返回虚拟化坐标，光圈会偏离真实指针
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        pass


def boost_scheduling():
    # 录屏+建库/OCR 抢 CPU 时，后台进程会被节能节流+定时器合并，sleep 严重超时导致光圈卡顿
    kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), 0x80)  # HIGH_PRIORITY_CLASS
    try:
        state = POWER_THROTTLING_STATE(1, 1, 0)  # 关闭 EXECUTION_SPEED 节流
        kernel32.SetProcessInformation(kernel32.GetCurrentProcess(), 4,
                                       ctypes.byref(state), ctypes.sizeof(state))
    except OSError:
        pass
    try:
        return kernel32.CreateWaitableTimerExW(None, None, 0x2, 0x1F0003) or None
    except (AttributeError, OSError):
        return None


def build_frames():
    dist = [int(math.hypot(x - CENTER, y - CENTER) * 2)
            for y in range(SIZE) for x in range(SIZE)]
    max_q = max(dist)
    r, g, b = COLOR
    # UpdateLayeredWindow 要求预乘 alpha 的 BGRA
    px = [bytes((b * a // 255, g * a // 255, r * a // 255, a)) for a in range(256)]

    def ring(q, radius, thick, amp):
        v = (thick / 2 + 1.2 - abs(q / 2 - radius)) / 1.2
        return int(amp * min(v, 1.0)) if v > 0 else 0

    n = 14
    frames = []
    for i in range(n + 1):
        t = i / n
        lut = []
        for q in range(max_q + 1):
            a = ring(q, RADIUS, THICKNESS, OPACITY)
            if q / 2 < RADIUS - THICKNESS / 2:
                a = max(a, FILL)
            if i:
                rr = RADIUS + RADIUS * RIPPLE_EXPAND * t
                a = max(a, ring(q, rr, THICKNESS * (1 - 0.4 * t),
                                int(OPACITY * (1 - t) ** 1.5)))
            lut.append(a)
        frames.append(b"".join(px[lut[q]] for q in dist))
    return frames


def main():
    set_dpi_aware()
    timer = boost_scheduling()
    frames = build_frames()

    wndproc = WNDPROC(lambda h, m, w, l: user32.DefWindowProcW(h, m, w, l))
    wc = WNDCLASSW()
    wc.lpfnWndProc = wndproc
    wc.hInstance = kernel32.GetModuleHandleW(None)
    wc.lpszClassName = CLASS_NAME
    if not user32.RegisterClassW(ctypes.byref(wc)):
        raise ctypes.WinError()

    hwnd = user32.CreateWindowExW(
        WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
        CLASS_NAME, None, WS_POPUP, 0, 0, SIZE, SIZE, None, None, wc.hInstance, None)
    if not hwnd:
        raise ctypes.WinError()

    screen_dc = user32.GetDC(None)
    mem_dc = gdi32.CreateCompatibleDC(screen_dc)
    bmi = BITMAPINFOHEADER()
    bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.biWidth = SIZE
    bmi.biHeight = -SIZE  # 负高度 = 自顶向下，字节序第 0 行即屏幕上方
    bmi.biPlanes = 1
    bmi.biBitCount = 32
    bits = ctypes.c_void_p()
    hbmp = gdi32.CreateDIBSection(screen_dc, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
    if not hbmp:
        raise ctypes.WinError()
    old_bmp = gdi32.SelectObject(mem_dc, hbmp)

    blend = BLENDFUNCTION(0, 0, 255, 1)
    src_pt = wt.POINT(0, 0)
    win_size = wt.SIZE(SIZE, SIZE)
    nbytes = SIZE * SIZE * 4

    ok_h = user32.RegisterHotKey(None, 1, MOD_CONTROL | MOD_ALT, ord('H'))
    ok_q = user32.RegisterHotKey(None, 2, MOD_CONTROL | MOD_ALT, ord('Q'))
    print("鼠标光圈已启动：淡黄圆环跟随鼠标，点击时扩散。", flush=True)
    print("  Ctrl+Alt+H 显示/隐藏" + ("" if ok_h else "（注册失败，可能被其他软件占用）"), flush=True)
    print("  Ctrl+Alt+Q 退出" + ("" if ok_q else "（注册失败，可能被其他软件占用）"), flush=True)
    print("  改颜色/大小：编辑脚本顶部 COLOR / RADIUS / OPACITY 常量。", flush=True)
    print("  OBS 请用「显示器采集」，游戏采集抓不到叠加层。", flush=True)

    user32.ShowWindow(hwnd, SW_SHOWNA)
    winmm.timeBeginPeriod(1)
    visible = True
    quitting = False
    prev_l = prev_r = False
    ripple_t0 = -1.0
    last = None
    msg = wt.MSG()
    try:
        while not quitting:
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                if msg.message == WM_HOTKEY:
                    if msg.wParam == 1:
                        visible = not visible
                        user32.ShowWindow(hwnd, SW_SHOWNA if visible else SW_HIDE)
                        last = None
                    elif msg.wParam == 2:
                        quitting = True
            pt = wt.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            now = time.perf_counter()
            l = bool(user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000)
            r = bool(user32.GetAsyncKeyState(VK_RBUTTON) & 0x8000)
            if (l and not prev_l) or (r and not prev_r):
                ripple_t0 = now
            prev_l, prev_r = l, r
            if visible:
                idx = 0
                if ripple_t0 >= 0:
                    t = (now - ripple_t0) / RIPPLE_SECONDS
                    if t >= 1:
                        ripple_t0 = -1.0
                    else:
                        idx = min(int(t * len(frames)), len(frames) - 1)
                key = (pt.x, pt.y, idx)
                if key != last:
                    ctypes.memmove(bits, frames[idx], nbytes)
                    dst = wt.POINT(pt.x - CENTER, pt.y - CENTER)
                    ret = user32.UpdateLayeredWindow(
                        hwnd, screen_dc, ctypes.byref(dst), ctypes.byref(win_size),
                        mem_dc, ctypes.byref(src_pt), 0, ctypes.byref(blend), ULW_ALPHA)
                    if not ret and last is None:
                        raise ctypes.WinError()  # 首帧就失败 = 光圈永远不可见，不能静默
                    last = key
            if timer:
                due = ctypes.c_longlong(-80000)  # 相对时间，100ns 单位 → 8ms
                kernel32.SetWaitableTimer(timer, ctypes.byref(due), 0, None, None, False)
                kernel32.WaitForSingleObject(timer, 0xFFFFFFFF)
            else:
                time.sleep(0.008)
    except KeyboardInterrupt:
        pass
    finally:
        winmm.timeEndPeriod(1)
        if timer:
            kernel32.CloseHandle(timer)
        user32.UnregisterHotKey(None, 1)
        user32.UnregisterHotKey(None, 2)
        user32.DestroyWindow(hwnd)
        gdi32.SelectObject(mem_dc, old_bmp)
        gdi32.DeleteObject(hbmp)
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(None, screen_dc)


if __name__ == "__main__":
    main()