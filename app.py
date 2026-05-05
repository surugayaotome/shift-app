import streamlit as st
import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.pool import NullPool
import io
from openpyxl import Workbook
from openpyxl.styles import PatternFill, Font
import datetime

st.set_page_config(page_title="日本橋乙女 シフト管理", layout="wide")

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
# 1. データベース接続＆初期化
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
        return create_engine(url_object, poolclass=NullPool)
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
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS staff_master (
                staff_name TEXT PRIMARY KEY,
                password TEXT NOT NULL,
                role_name TEXT,
                is_admin BOOLEAN DEFAULT FALSE
            );
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS system_config (
                config_key TEXT PRIMARY KEY,
                config_value TEXT
            );
        """))
        result = conn.execute(text("SELECT COUNT(*) FROM staff_master")).scalar()
        if result == 0:
            conn.execute(text("""
                INSERT INTO staff_master (staff_name, password, role_name, is_admin) 
                VALUES ('店長', 'admin1234', '全体統括', TRUE)
            """))
        conn.commit()

init_db()

def load_staff():
    return pd.read_sql("SELECT * FROM staff_master", engine)

def load_day_data(day_str):
    return pd.read_sql(f"SELECT * FROM shift_data WHERE day = '{day_str}'", engine)

def save_day_data(day_str, df):
    with engine.connect() as conn:
        conn.execute(text(f"DELETE FROM shift_data WHERE day = '{day_str}'"))
        for _, row in df.iterrows():
            shift_values = ",".join([str(row.get(t, "")) for t in time_slots])
            conn.execute(text("""
                INSERT INTO shift_data (day, staff_name, off_status, shift_json)
                VALUES (:day, :staff_name, :off_status, :shift_json)
            """), {"day": day_str, "staff_name": row["氏名"], "off_status": row["状態"], "shift_json": shift_values})
        conn.commit()

def save_config(key, value):
    with engine.connect() as conn:
        conn.execute(text("""
            INSERT INTO system_config (config_key, config_value) 
            VALUES (:key, :value) 
            ON CONFLICT (config_key) DO UPDATE SET config_value = :value
        """), {"key": key, "value": str(value)})
        conn.commit()

def get_config(key, default_value=""):
    with engine.connect() as conn:
        result = conn.execute(text("SELECT config_value FROM system_config WHERE config_key = :key"), {"key": key}).scalar()
        return result if result else default_value

# ==========================================
# 2. セッション管理
# ==========================================
if "user" not in st.session_state:
    st.session_state.user = None

# 時間帯のスロット
time_slots = [f"{h}:{m:02d}" for h in range(10, 18) for m in (0, 30)]

if st.session_state.user is None:
    st.title("🔐 ログイン")
    with st.form("login_form"):
        input_name = st.text_input("氏名")
        input_pass = st.text_input("パスワード", type="password")
        submit = st.form_submit_button("ログイン", type="primary")
        if submit:
            staff_df = load_staff()
            user_row = staff_df[(staff_df['staff_name'] == input_name) & (staff_df['password'] == input_pass)]
            if not user_row.empty:
                st.session_state.user = {
                    "name": user_row.iloc[0]['staff_name'],
                    "is_admin": user_row.iloc[0]['is_admin'],
                    "role": user_row.iloc[0]['role_name']
                }
                st.rerun()
            else:
                st.error("氏名またはパスワードが間違っています。")
    st.stop()

st.sidebar.write(f"👤 **{st.session_state.user['name']}** さん")
st.sidebar.write(f"🏷️ 担当: {st.session_state.user['role']}")
if st.sidebar.button("ログアウト"):
    st.session_state.user = None
    st.rerun()

staff_df = load_staff()
staff_list = staff_df['staff_name'].tolist()

# ==========================================
# 3. 👨‍💼 管理者ダッシュボード
# ==========================================
if st.session_state.user["is_admin"]:
    st.title("👨‍💼 管理者ダッシュボード")
    tab1, tab2, tab3, tab4 = st.tabs(["📝 シフト編集", "📅 募集設定", "👥 スタッフ管理", "📊 Excel出力"])
    
    # 【タブ1】シフト編集（名前列固定の横長Excelライク版）
    with tab1:
        st.write("### 📝 シフト編集（日付選択）")
        today = datetime.date.today()
        # 過去3ヶ月〜未来3ヶ月まで選択可能
        target_date = st.date_input(
            "カレンダーから編集・確認したい日付を選択してください", 
            value=today,
            min_value=today - datetime.timedelta(days=90),
            max_value=today + datetime.timedelta(days=90)
        )
        
        target_day_str = target_date.strftime("%Y-%m-%d")
        raw_df = load_day_data(target_day_str)
        
        display_data = []
        for s in staff_list:
            match = raw_df[raw_df["staff_name"] == s]
            if not match.empty:
                # 登録があれば「OFF」か「提出済」
                status = match.iloc[0]["off_status"]
                if status not in ["OFF", "未提出"]: status = "提出済"
                row = {"氏名": s, "状態": status}
                slots = match.iloc[0]["shift_json"].split(",")
                for j, t in enumerate(time_slots):
                    row[t] = slots[j] if j < len(slots) else ""
            else:
                # 登録がなければ「未提出」
                row = {"氏名": s, "状態": "未提出", **{t: "" for t in time_slots}}
            display_data.append(row)
        
        df_to_edit = pd.DataFrame(display_data)
        # 💡 これがスクロールしても名前が消えない魔法（インデックス化）
        df_to_edit.set_index("氏名", inplace=True)
        
        col_config = {
            "状態": st.column_config.SelectboxColumn("状態", options=["未提出", "提出済", "OFF"], width="small", required=True)
        }
        for t in time_slots: 
            col_config[t] = st.column_config.SelectboxColumn(t, options=["", "1", "2", "同", "休"], width="small")
        
        st.info("💡 横にスクロールしても「氏名」と「状態」は固定されて見えます。")
        edited_df = st.data_editor(
            df_to_edit,
            column_config=col_config,
            use_container_width=True,
            key=f"editor_{target_day_str}"
        )
        
        if st.button(f"💾 {target_day_str} のシフトを保存", type="primary"):
            # インデックス（氏名）を列に戻して保存関数へ渡す
            save_df = edited_df.reset_index()
            save_day_data(target_day_str, save_df)
            st.success(f"✅ {target_day_str} のデータを更新しました！")

    # 【タブ2】募集設定
    with tab2:
        st.write("従業員画面に表示するシフト提出の対象期間を設定します。")
        col1, col2 = st.columns(2)
        with col1:
            period = st.date_input("📅 シフト対象期間 (開始日 - 終了日)", value=(today, today + datetime.timedelta(days=6)))
        with col2:
            deadline = st.date_input("⏳ 提出締め切り日", value=today + datetime.timedelta(days=3))
            
        if st.button("募集設定を更新", type="primary"):
            if isinstance(period, tuple) and len(period) == 2:
                save_config("period_start", period[0].strftime("%Y-%m-%d"))
                save_config("period_end", period[1].strftime("%Y-%m-%d"))
                save_config("deadline", deadline.strftime("%Y-%m-%d"))
                st.success("✅ 募集設定を更新しました！")
            else:
                st.error("⚠️ 期間の「開始日」と「終了日」の両方を選択してください。")

    # 【タブ3】スタッフ管理（省略なし）
    with tab3:
        st.write("#### 📁 CSVで一括インポート")
        @st.cache_data
        def get_csv_template():
            df_template = pd.DataFrame(columns=["氏名", "パスワード", "担当", "管理者権限"])
            df_template.loc[0] = ["テスト太郎", "pass123", "ホール", "FALSE"]
            return df_template.to_csv(index=False).encode('utf-8-sig')
            
        st.download_button("📥 インポート用CSVテンプレート", data=get_csv_template(), file_name="staff_template.csv", mime="text/csv")
        uploaded_file = st.file_uploader("CSVファイルをアップロード", type=["csv"])
        
        if uploaded_file:
            try:
                df_csv = pd.read_csv(uploaded_file)
                if list(df_csv.columns)[:4] != ["氏名", "パスワード", "担当", "管理者権限"]:
                    st.error("⚠️ 列の定義が異なります。")
                else:
                    if st.button("CSVデータをデータベースに保存", type="primary"):
                        with engine.connect() as conn:
                            for _, row in df_csv.iterrows():
                                is_admin_val = str(row["管理者権限"]).strip().lower() in ['true', '1', 'yes', 'はい']
                                conn.execute(text("""
                                    INSERT INTO staff_master (staff_name, password, role_name, is_admin)
                                    VALUES (:name, :pass, :role, :is_admin)
                                    ON CONFLICT (staff_name) DO UPDATE 
                                    SET password = :pass, role_name = :role, is_admin = :is_admin
                                """), {"name": str(row["氏名"]), "pass": str(row["パスワード"]), "role": str(row["担当"]), "is_admin": is_admin_val})
                            conn.commit()
                        st.success("✅ CSVからのインポートが完了しました！")
                        st.rerun()
            except Exception as e:
                st.error(f"エラー: {e}")
        
        st.write("---")
        st.write("#### ✍️ 手動編集")
        edited_staff = st.data_editor(staff_df, num_rows="dynamic", hide_index=True)
        if st.button("手動編集を保存"):
            with engine.connect() as conn:
                conn.execute(text("DELETE FROM staff_master")) 
                for _, row in edited_staff[edited_staff['staff_name'].str.strip() != ""].iterrows():
                    conn.execute(text("""
                        INSERT INTO staff_master (staff_name, password, role_name, is_admin)
                        VALUES (:name, :pass, :role, :is_admin)
                    """), {"name": row["staff_name"], "pass": row["password"], "role": row["role_name"], "is_admin": row["is_admin"]})
                conn.commit()
            st.success("✅ スタッフ情報を更新しました！")
            st.rerun()

    # 【タブ4】Excel出力（期間選択式）
    with tab4:
        st.write("指定した期間のシフトをExcelとしてダウンロードします。")
        ex_start = st.date_input("開始日", value=today)
        ex_end = st.date_input("終了日", value=today + datetime.timedelta(days=6))
        
        def create_excel_file(start_date, end_date):
            output = io.BytesIO()
            wb = Workbook()
            wb.remove(wb.active)
            
            delta = end_date - start_date
            for i in range(delta.days + 1):
                d_date = start_date + datetime.timedelta(days=i)
                d_str = d_date.strftime("%Y-%m-%d")
                ws = wb.create_sheet(title=d_date.strftime("%m月%d日"))
                
                headers = ["氏名", "状態"] + time_slots
                ws.append(headers)
                df = load_day_data(d_str)
                
                for s in staff_list:
                    match = df[df["staff_name"] == s]
                    if not match.empty:
                        status = match.iloc[0]["off_status"]
                        if status not in ["OFF", "未提出"]: status = "提出済"
                        shift_vals = match.iloc[0]["shift_json"].split(",")
                        row_data = [s, status] + [shift_vals[i] if i < len(shift_vals) else "" for i in range(len(time_slots))]
                    else:
                        row_data = [s, "未提出"] + [""] * len(time_slots)
                    ws.append(row_data)
                    
                for cell in ws[1]:
                    cell.font = Font(bold=True)
                    cell.fill = PatternFill(start_color="F0F0F0", end_color="F0F0F0", fill_type="solid")
                ws.column_dimensions['A'].width = 15
            wb.save(output)
            return output.getvalue()

        if ex_end >= ex_start:
            st.download_button(
                label="📥 指定期間のシフトをExcel出力",
                data=create_excel_file(ex_start, ex_end),
                file_name=f"シフト表_{ex_start.strftime('%m%d')}-{ex_end.strftime('%m%d')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary"
            )
        else:
            st.error("終了日は開始日以降にしてください。")

# ==========================================
# 4. 📱 従業員メニュー
# ==========================================
else:
    st.title("📱 シフト希望提出")
    st.info(f"ログイン中のユーザー: **{st.session_state.user['name']}** さん")
    
    p_start_str = get_config("period_start")
    p_end_str = get_config("period_end")
    p_deadline = get_config("deadline", "未設定")
    
    if p_start_str and p_end_str:
        st.warning(f"**現在の募集期間:** {p_start_str} 〜 {p_end_str} 　🚨 **提出締切:** {p_deadline}")
        
        p_start = datetime.datetime.strptime(p_start_str, "%Y-%m-%d").date()
        p_end = datetime.datetime.strptime(p_end_str, "%Y-%m-%d").date()
        
        # 期間内の日付リストを生成
        target_dates = []
        delta = p_end - p_start
        for i in range(delta.days + 1):
            target_dates.append((p_start + datetime.timedelta(days=i)).strftime("%Y-%m-%d"))
            
        off_days = st.multiselect("お休み（OFF）希望の日付を選んでください", target_dates)
        
        if st.button("シフトを提出する", type="primary"):
            my_name = st.session_state.user["name"]
            for d_str in target_dates:
                df = load_day_data(d_str)
                # 休みに選んだ日は「OFF」、それ以外は出勤可能なので「提出済」
                status = "OFF" if d_str in off_days else "提出済"
                
                if my_name in df["staff_name"].values:
                    df.loc[df["staff_name"] == my_name, "off_status"] = status
                else:
                    new_row = pd.DataFrame([{"day": d_str, "staff_name": my_name, "off_status": status, "shift_json": ",".join([""]*len(time_slots))}])
                    df = pd.concat([df, new_row], ignore_index=True)
                
                save_df = pd.DataFrame([{"氏名": r["staff_name"], "状態": r["off_status"], **dict(zip(time_slots, r["shift_json"].split(",")))} for _, r in df.iterrows()])
                save_day_data(d_str, save_df)
                
            st.success("✅ あなたのシフト希望を提出しました！")
    else:
        st.error("現在、管理者によって設定されたシフト募集期間がありません。")
    
