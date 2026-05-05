import streamlit as st
import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
import urllib.parse
from openpyxl import Workbook
from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
import io

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

# ==========================================
# 1. データベース接続＆操作関数
# ==========================================
@st.cache_resource
def get_engine():
    try:
        raw_uri = st.secrets["database"]["uri"]
        prefix, rest = raw_uri.split("://")
        user_pass, host_db = rest.rsplit("@", 1)
        user, password = user_pass.split(":", 1)
        host_port, db_query = host_db.split("/", 1)
        host, port = host_port.split(":")
        db_name = db_query.split("?")[0]

        url_object = URL.create(
            drivername="postgresql+psycopg2",
            username=user,
            password=password,
            host=host,
            port=int(port),
            database=db_name,
            query={"sslmode": "require"},
        )
        return create_engine(url_object)
    except Exception as e:
        st.error(f"接続設定エラー: {e}")
        return None

engine = get_engine()

def init_db():
    if engine is None: return
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

# データを読み込む関数
def load_weekly_data(day):
    query = f"SELECT * FROM shift_data WHERE day = '{day}'"
    return pd.read_sql(query, engine)

# データを保存する関数
def save_day_data(day, df):
    with engine.connect() as conn:
        conn.execute(text(f"DELETE FROM shift_data WHERE day = '{day}'"))
        for _, row in df.iterrows():
            shift_values = ",".join([str(row.get(t, "")) for t in time_slots])
            conn.execute(text("""
                INSERT INTO shift_data (day, staff_name, off_status, shift_json)
                VALUES (:day, :staff_name, :off_status, :shift_json)
            """), {"day": day, "staff_name": row["氏名"], "off_status": row["休み"], "shift_json": shift_values})
        conn.commit()

# ==========================================
# 2. 基本設定
# ==========================================
days_of_week = ["月", "火", "水", "木", "金", "土", "日"]
staff_list = ["奥村幸子", "宮崎春秋代", "陰山爽", "徳永久玲美", "北田詩歩", "寺前吏紗", "上田鈴奈", "小鳥美貴"]
time_slots = [f"{h}:{m:02d}" for h in range(10, 18) for m in (0, 30)]

role = st.sidebar.radio("▼ 画面切り替え", ["📱 従業員画面", "👨‍💼 管理者画面"])

# ==========================================
# 3. 📱 従業員画面：希望提出
# ==========================================
if role == "📱 従業員画面":
    st.title("📱 従業員用：希望シフト提出")
    st.info("お休みの希望曜日を選んで「提出」を押してください。")
    
    staff_name = st.selectbox("あなたの名前を選択してください", [""] + staff_list)
    
    if staff_name:
        off_days = st.multiselect("お休み（OFF）希望の曜日", days_of_week)
        
        if st.button("シフトを提出する", type="primary"):
            for d in days_of_week:
                # 既存データを読み込んで、自分の行だけ更新または追加する
                df = load_weekly_data(d)
                off_status = "OFF" if d in off_days else ""
                
                if staff_name in df["staff_name"].values:
                    df.loc[df["staff_name"] == staff_name, "off_status"] = off_status
                else:
                    new_row = pd.DataFrame([{"day": d, "staff_name": staff_name, "off_status": off_status, "shift_json": ",".join([""]*len(time_slots))}])
                    df = pd.concat([df, new_row], ignore_index=True)
                
                # DB保存用に整形
                save_df = pd.DataFrame([{"氏名": r["staff_name"], "休み": r["off_status"], **dict(zip(time_slots, r["shift_json"].split(",")))} for _, r in df.iterrows()])
                save_day_data(d, save_df)
                
            st.success("✅ シフト希望をデータベースに保存しました！")

# ==========================================
# 4. 👨‍💼 管理者画面：編集と出力
# ==========================================
elif role == "👨‍💼 管理者画面":
    st.title("👨‍💼 管理者：シフト編集・Excel出力")
    
    tabs = st.tabs(days_of_week)
    for i, d in enumerate(days_of_week):
        with tabs[i]:
            st.write(f"### {d}曜日のシフト")
            
            # DBから読み込んで表示用に整形
            raw_df = load_weekly_data(d)
            display_data = []
            for s in staff_list:
                match = raw_df[raw_df["staff_name"] == s]
                if not match.empty:
                    row = {"氏名": s, "休み": match.iloc[0]["off_status"]}
                    slots = match.iloc[0]["shift_json"].split(",")
                    for j, t in enumerate(time_slots):
                        row[t] = slots[j] if j < len(slots) else ""
                else:
                    row = {"氏名": s, "休み": "", **{t: "" for t in time_slots}}
                display_data.append(row)
            
            df_to_edit = pd.DataFrame(display_data)
            
            # 編集UIの設定
            col_config = {
                "氏名": st.column_config.TextColumn(disabled=True), 
                "休み": st.column_config.TextColumn(disabled=True)
            }
            for t in time_slots: 
                col_config[t] = st.column_config.SelectboxColumn(t, options=["", "1", "2", "同", "休"], width="small")
            
            # データエディタの表示
            edited_df = st.data_editor(df_to_edit, column_config=col_config, hide_index=True, key=f"editor_{d}")
            
            # 保存ボタン
            if st.button(f"💾 {d}曜日の変更を保存"):
                save_day_data(d, edited_df)
                st.toast(f"✅ {d}曜日のデータを更新しました！")

    # === 管理者画面の一番下（st.warning を消して以下を追加） ===
    st.write("---")
    st.write("### 📊 シフト表のExcel出力")
    
    # Excel生成関数
    def create_excel_file():
        output = io.BytesIO()
        wb = Workbook()
        wb.remove(wb.active) # デフォルトの空シートを削除
        
        for d in days_of_week:
            ws = wb.create_sheet(title=f"{d}曜日")
            
            # 1行目：ヘッダーの書き込み
            headers = ["氏名", "休み"] + time_slots
            ws.append(headers)
            
            # DBから最新のデータを取得して書き込み
            df = load_weekly_data(d)
            for s in staff_list:
                match = df[df["staff_name"] == s]
                if not match.empty:
                    off_val = match.iloc[0]["off_status"]
                    shift_vals = match.iloc[0]["shift_json"].split(",")
                    # 空白を埋めてサイズを合わせる
                    row_data = [s, off_val] + [shift_vals[i] if i < len(shift_vals) else "" for i in range(len(time_slots))]
                else:
                    row_data = [s, ""] + [""] * len(time_slots)
                
                ws.append(row_data)
            
            # 簡単な装飾（ヘッダーを太字＆グレー背景にして見やすく）
            for cell in ws[1]:
                cell.font = Font(bold=True)
                cell.fill = PatternFill(start_color="F0F0F0", end_color="F0F0F0", fill_type="solid")
                
            # 列幅の調整（氏名列だけ少し広く）
            ws.column_dimensions['A'].width = 15
                
        wb.save(output)
        return output.getvalue()

    # Streamlitのダウンロードボタン
    excel_data = create_excel_file()
    st.download_button(
        label="📥 全曜日のシフトをExcelでダウンロード",
        data=excel_data,
        file_name="シフト表_最新版.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary" # ここをprimaryにすることで、設定した赤色ボタンになります
    )
