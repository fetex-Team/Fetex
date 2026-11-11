"""학습 기간 선택 타임라인 위젯 — '학습만 실행' 창에서 실측 통계로 만들 학습 기간을 고른다.

KJS 브랜치의 TimelineRangePicker를 jwy 학습 창에 맞게 옮겼다.
- 1년 막대 위에서 기간 막대를 클릭·드래그로 옮긴다.
- 고정 길이(2·4·8주) 모드: 길이를 유지한 채 ±1주 이동. 해제하면 시작·끝 날짜를 직접 입력.
- 평가 날짜(sim_date)를 빨간 선으로 표시하고, 학습 기간이 평가 날짜와 겹치면 경고한다(누수 방지).
"""
import datetime as dt
import tkinter as tk
from tkinter import ttk

WEEK_CHOICES = (2, 4, 8)


def _to_date(v):
    if isinstance(v, dt.date):
        return v
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


class TimelineRangePicker(ttk.Frame):
    def __init__(self, parent, start=None, end=None, eval_date=None):
        super().__init__(parent)
        self.eval_date = _to_date(eval_date)
        s, e = _to_date(start), _to_date(end)
        ref = s or self.eval_date or dt.date.today()
        self.year = ref.year
        self.base = dt.date(self.year, 1, 1)
        self.total = (dt.date(self.year + 1, 1, 1) - self.base).days

        if s and e and s <= e and s.year == e.year == self.year:
            self.s, self.e = (s - self.base).days, (e - self.base).days
        else:   # 기본: 평가 날짜 바로 전까지 8주
            last = (self.eval_date - dt.timedelta(days=1)) if self.eval_date else ref
            self.e = max(55, min(self.total - 1, (last - self.base).days))
            self.s = self.e - 55
        length = self.e - self.s + 1
        self.fixed_var = tk.BooleanVar(value=length in [w * 7 for w in WEEK_CHOICES])
        self.weeks_var = tk.IntVar(value=length // 7 if self.fixed_var.get() else 8)
        self.start_text = tk.StringVar()
        self.end_text = tk.StringVar()
        self.width = 640
        self._build()
        self._on_mode()

    # ------------------------------------------------------------------ 화면
    def _build(self):
        self.canvas = tk.Canvas(self, height=74, bg="#f8fafc", highlightthickness=1,
                                highlightbackground="#cbd5e1", cursor="hand2")
        self.canvas.pack(fill="x", pady=(2, 6))
        self.canvas.bind("<Configure>", self._on_resize)
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<B1-Motion>", self._on_click)

        top = ttk.Frame(self)
        top.pack(fill="x", pady=2)
        ttk.Checkbutton(top, text="고정 길이", variable=self.fixed_var, command=self._on_mode).pack(side="left")
        self.week_btns = []
        for w in WEEK_CHOICES:
            rb = ttk.Radiobutton(top, text=f"{w}주 ({w * 7}일)", value=w, variable=self.weeks_var,
                                 command=self._on_weeks)
            rb.pack(side="left", padx=6)
            self.week_btns.append(rb)

        self.move_row = ttk.Frame(self)
        ttk.Button(self.move_row, text="◀ 1주", width=8, command=lambda: self._shift(-7)).pack(side="left", padx=2)
        ttk.Button(self.move_row, text="1주 ▶", width=8, command=lambda: self._shift(7)).pack(side="left", padx=2)
        if self.eval_date:
            ttk.Button(self.move_row, text="평가 날짜 바로 전까지", command=self._snap_before_eval).pack(side="left", padx=8)

        self.free_row = ttk.Frame(self)
        ttk.Label(self.free_row, text="시작").pack(side="left")
        ttk.Entry(self.free_row, textvariable=self.start_text, width=12).pack(side="left", padx=4)
        ttk.Label(self.free_row, text="끝").pack(side="left", padx=(8, 0))
        ttk.Entry(self.free_row, textvariable=self.end_text, width=12).pack(side="left", padx=4)
        ttk.Button(self.free_row, text="적용", command=self._apply_text).pack(side="left", padx=4)
        ttk.Label(self.free_row, text="(YYYY-MM-DD, 막대를 클릭하면 가까운 쪽 끝이 움직여요)",
                  foreground="#64748b").pack(side="left", padx=4)

        self.info = ttk.Label(self, justify="left")
        self.info.pack(anchor="w", pady=(6, 0))
        self.warn = ttk.Label(self, foreground="#b91c1c", justify="left")
        self.warn.pack(anchor="w")

    def _on_resize(self, event):
        if event.width > 50:
            self.width = event.width
            self._redraw()

    # ------------------------------------------------------------------ 조작
    def _length(self):
        return self.weeks_var.get() * 7

    def _place(self, start):
        n = self._length()
        self.s = max(0, min(start, self.total - n))
        self.e = self.s + n - 1
        self._redraw()

    def _on_mode(self):
        if self.fixed_var.get():
            self.free_row.pack_forget()
            self.move_row.pack(fill="x", pady=2, before=self.info)
            for b in self.week_btns:
                b.state(["!disabled"])
            self._place(self.s)
        else:
            self.move_row.pack_forget()
            self.free_row.pack(fill="x", pady=2, before=self.info)
            for b in self.week_btns:
                b.state(["disabled"])
            self._redraw()

    def _on_weeks(self):
        # 끝 날짜를 유지하고 길이만 바꾼다 (보통 평가 날짜 직전에 맞춰 두므로)
        self._place(self.e - self._length() + 1)

    def _shift(self, days):
        self._place(self.s + days)

    def _snap_before_eval(self):
        last = (self.eval_date - dt.timedelta(days=1) - self.base).days
        self._place(last - self._length() + 1)

    def _apply_text(self):
        s, e = _to_date(self.start_text.get()), _to_date(self.end_text.get())
        if not s or not e or s > e:
            self.warn.config(text="날짜 형식이 맞지 않거나 시작이 끝보다 늦어요.")
            return
        lo, hi = self.base, dt.date(self.year, 12, 31)
        s, e = max(lo, min(s, hi)), max(lo, min(e, hi))
        self.s, self.e = (s - self.base).days, (e - self.base).days
        self._redraw()

    def _day_at(self, x):
        m = 14
        w = max(10, self.width - 2 * m)
        return max(0, min(self.total - 1, int((min(max(x, m), m + w) - m) / w * self.total)))

    def _on_click(self, event):
        day = self._day_at(event.x)
        if self.fixed_var.get():
            self._place(day - self._length() // 2)
        else:
            if abs(day - self.s) <= abs(day - self.e):
                self.s = min(day, self.e)
            else:
                self.e = max(day, self.s)
            self._redraw()

    # ------------------------------------------------------------------ 그리기
    def _x(self, day):
        m = 14
        return m + day / self.total * max(10, self.width - 2 * m)

    def _redraw(self):
        c = self.canvas
        c.delete("all")
        y1, y2 = 22, 46
        c.create_rectangle(self._x(0), y1, self._x(self.total), y2, fill="#e2e8f0", outline="#cbd5e1")
        for mth in range(1, 13):
            d0 = (dt.date(self.year, mth, 1) - self.base).days
            d1 = ((dt.date(self.year + 1, 1, 1) if mth == 12 else dt.date(self.year, mth + 1, 1)) - self.base).days
            c.create_line(self._x(d0), y1, self._x(d0), y2, fill="#94a3b8")
            c.create_text((self._x(d0) + self._x(d1)) / 2, 60, text=f"{mth}월", font=("맑은 고딕", 8), fill="#64748b")

        x1, x2 = self._x(self.s), self._x(self.e + 1)
        c.create_rectangle(x1, y1 - 2, x2, y2 + 2, fill="#2563eb", outline="#1d4ed8", width=2)
        n = self.e - self.s + 1
        if x2 - x1 >= 56:
            c.create_text((x1 + x2) / 2, (y1 + y2) / 2, text=f"{n}일", fill="white", font=("맑은 고딕", 9, "bold"))

        if self.eval_date and self.eval_date.year == self.year:
            d = (self.eval_date - self.base).days
            xe = self._x(d)
            c.create_line(xe, y1 - 8, xe, y2 + 6, fill="#dc2626", width=2)
            c.create_text(xe + 3, 10, text=f"평가 {self.eval_date:%m/%d}", anchor="w", fill="#dc2626",
                          font=("맑은 고딕", 8, "bold"))

        s, e = self.selection()[:2]
        self.start_text.set(s.isoformat())
        self.end_text.set(e.isoformat())
        weekend = sum((s + dt.timedelta(days=i)).weekday() >= 5 for i in range(n))
        self.info.config(text=f"학습 기간: {s} ~ {e}  ({n}일 = {n / 7:.1f}주, 평일 {n - weekend}일 · 주말 {weekend}일)")
        if self.eval_date and e >= self.eval_date:
            self.warn.config(text=f"⚠ 학습 기간이 평가 날짜({self.eval_date}) 이후까지 들어가요. "
                                  "평가 날짜가 학습에 섞이면 결과가 실제보다 좋게 나와요.")
        else:
            self.warn.config(text="")

    def selection(self):
        """(시작 date, 끝 date, 일수)"""
        s = self.base + dt.timedelta(days=self.s)
        e = self.base + dt.timedelta(days=self.e)
        return s, e, (e - s).days + 1

    def overlaps_eval(self):
        return bool(self.eval_date and self.selection()[1] >= self.eval_date)
