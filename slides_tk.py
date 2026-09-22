# Deterministic slide window for E2E visual tests (tkinter, no deps).
# Cycles course-like slides every SLIDE_S seconds at a fixed position.
import tkinter as tk

SLIDE_S = 18
SLIDES = [
    ("第1页 二次函数", "形如 y = ax^2 + bx + c (a≠0)\n叫做x的二次函数。\n图像是抛物线。"),
    ("第2页 判别式", "Δ = b^2 - 4ac\nΔ>0 两个不等实根\nΔ=0 两个相等实根\nΔ<0 无实根"),
    ("第3页 例题", "例：y = 2x^2 - 4x + 1\n求对称轴与顶点\n对称轴 x = -b/2a = 1"),
    ("第4页 强调", "注意：考试重点！\n1. a 不能等于 0\n2. 配方求顶点\n3. Δ 与根的关系"),
]

root = tk.Tk()
root.title("SlideDeck-E2E")
root.geometry("860x520+120+120")
root.attributes("-topmost", True)

title = tk.Label(root, text="", font=("Microsoft YaHei", 28, "bold"), fg="#111", bg="#f5f5f5")
title.pack(fill="x", pady=(20, 8))
body = tk.Label(root, text="", font=("Microsoft YaHei", 22), fg="#222", bg="#f5f5f5", justify="left")
body.pack(fill="both", expand=True, padx=30, pady=10)
root.configure(bg="#f5f5f5")

idx = 0


def show():
    global idx
    t, b = SLIDES[idx % len(SLIDES)]
    title.configure(text=t)
    body.configure(text=b)
    idx += 1
    root.after(SLIDE_S * 1000, show)


show()
root.mainloop()
