import Quartz

def list_windows():
    # kCGWindowListOptionOnScreenOnly = 0
    # kCGWindowListOptionAll = 0
    
    options = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
    window_list = Quartz.CGWindowListCopyWindowInfo(options, Quartz.kCGNullWindowID)
    
    print(f"Found {len(window_list)} on-screen windows:")
    print("-" * 60)
    print(f"{'PID':<8} | {'ID':<10} | {'Layer':<6} | {'Owner Name':<20} | {'Title'}")
    print("-" * 60)
    
    for window in window_list:
        pid = window.get('kCGWindowOwnerPID', 0)
        wid = window.get('kCGWindowNumber', 0)
        layer = window.get('kCGWindowLayer', 0)
        owner = window.get('kCGWindowOwnerName', 'Unknown')
        title = window.get('kCGWindowName', '')
        
        # Only show relevant apps
        if layer == 0 and owner in ['Google Chrome', 'Code', 'Terminal', 'Stealth']:
             print(f"{pid:<8} | {wid:<10} | {layer:<6} | {owner:<20} | {title}")

if __name__ == "__main__":
    list_windows()
