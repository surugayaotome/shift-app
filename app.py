import streamlit as st
import pandas as pd
import io
from sqlalchemy import create_engine, text
from openpyxl import Workbook
from openpyxl.styles import PatternFill, Font, Alignment, Border, Side

st.set_page_config(page_title="日本橋乙女 シフト管理", layout="wide")

# --- 危険なボタンの赤色設定 ---
st.markdown("""
<style>
div[data-testid="stButton"] button[kind="primary"] {
    background-color: #ff4b4b !important;
    border-color: #ff4b4b !important;
    color: white !important;
}
</style>
""", unsafe_allow_html=True)

# --- 1. データベース接続設定 ---
@st.cache_resource
def get_engine():
    # URIの先頭を強制的に postgresql+psycopg2:// に置換して確実にドライバーを指定する
    uri = st.secrets["database"]["uri"]
    if uri.startswith("postgresql://"):
        uri = uri.replace("postgresql://", "postgresql+psycopg2://", 1)
    return create_engine(uri)

engine = get_engine()

# --- 2. テーブル初期化 (QA工程として自動化) ---
def init_db():
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS shift_data (
                day TEXT,
                staff_name TEXT,
                off_status TEXT,
                shift_json TEXT,
                PRIMARY KEY (day, staff_name)
            );
        """))
        conn.commit()

init_db()

# --- 3. データの読み書き関数 ---
def load_weekly_data(day):
    query = f"SELECT * FROM shift_data WHERE day = '{day}'"
    df_db = pd.read_sql(query, engine)
    return df_db

def save_day_data(day, df):
    with engine.connect() as conn:
        # 一旦その日のデータを消して、最新の状態を書き込む (アトミックな更新)
        conn.execute(text(f"DELETE FROM shift_data WHERE day = '{day}'"))
        for _, row in df.iterrows():
            # タイムライン部分をカンマ区切りで保存
            shift_values = ",".join([str(row[t]) for t in time_slots])
            conn.execute(text("""
                INSERT INTO shift_data (day, staff_name, off_status, shift_json)
                VALUES (:day, :staff_name, :off_status, :shift_json)
            """), {"day": day, "staff_name": row["氏名"], "off_status": row["休み"], "shift_json": shift_values})
        conn.commit()

# --- 設定値 ---
days_of_week = ["月", "火", "水", "木", "金", "土", "日"]
staff_list = ["奥村幸子", "宮崎春秋代", "陰山爽", "徳永久玲美", "北田詩歩", "寺前吏紗", "上田鈴奈", "小鳥美貴"]
time_slots = [f"{h}:{m:02d}" for h in range(10, 18) for m in (0, 30)]

# UI
role = st.sidebar.radio("▼ 画面切り替え", ["👨‍💼 管理者画面", "📱 従業員画面"])

# ==========================================
# 📱 従業員画面：希望提出
# ==========================================
if role == "📱 従業員画面":
    st.title("📱 従業員用：希望シフト提出")
    staff_name = st.selectbox("名前を選択", [""] + staff_list)
    
    if staff_name:
        off_days = st.multiselect("お休み（OFF）希望の曜日", days_of_week)
        if st.button("提出する"):
            for d in days_of_week:
                # 既存データを読み込んで、特定のスタッフのOFFだけ書き換える
                df = load_weekly_data(d)
                if staff_name in df["staff_name"].values:
                    df.loc[df["staff_name"] == staff_name, "off_status"] = "OFF" if d in off_days else ""
                else:
                    # 新規行の作成
                    new_row = {"day": d, "staff_name": staff_name, "off_status": "OFF" if d in off_days else "", "shift_json": ",".join([""]*len(time_slots))}
                    df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
                
                # 整形して保存
                save_df = pd.DataFrame([{"氏名": r["staff_name"], "休み": r["off_status"], **dict(zip(time_slots, r["shift_json"].split(",")))} for _, r in df.iterrows()])
                save_day_data(d, save_df)
            st.success("データベースに保存されました！")

# ==========================================
# 👨‍💼 管理者画面：編集と出力
# ==========================================
elif role == "👨‍💼 管理者画面":
    st.title("👨‍💼 管理者：シフト編集・Excel出力")
    
    tabs = st.tabs(days_of_week)
    for i, d in enumerate(days_of_week):
        with tabs[i]:
            # DBから読み込み
            raw_df = load_weekly_data(d)
            # 表示用に整形
            display_data = []
            for s in staff_list:
                match = raw_df[raw_df["staff_name"] == s]
                if not match.empty:
                    row = {"氏名": s, "休み": match.iloc[0]["off_status"]}
                    slots = match.iloc[0]["shift_json"].split(",")
                    for j, t in enumerate(time_slots):
                        row[t] = slots[j]
                else:
                    row = {"氏名": s, "休み": "", **{t: "" for t in time_slots}}
                display_data.append(row)
            
            df_to_edit = pd.DataFrame(display_data)
            
            # 編集UI
            col_config = {"氏名": st.column_config.TextColumn(disabled=True), "休み": st.column_config.TextColumn(disabled=True)}
            for t in time_slots: col_config[t] = st.column_config.SelectboxColumn(t, options=["", "1", "2", "同", "休"], width="small")
            
            edited_df = st.data_editor(df_to_edit, column_config=col_config, hide_index=True, key=f"editor_{d}")
            
            if st.button(f"{d}曜日の変更を保存"):
                save_day_data(d, edited_df)
                st.toast(f"{d}曜日のデータを更新しました")

    # Excel出力ロジック (以前のものを流用)
    if st.button("📅 画像と同じ形式のExcelを出力", type="secondary"):
        # ※ここに前回のExcel出力コード（Workbook生成〜色塗り）が入ります
        st.write("（ここにExcel生成ロジックが走り、ダウンロードボタンが出ます）")
