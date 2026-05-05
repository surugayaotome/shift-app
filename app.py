import streamlit as st
import pandas as pd
import io
from openpyxl import Workbook
from openpyxl.styles import PatternFill, Font, Alignment, Border, Side

st.set_page_config(page_title="シフト管理システム MVP", layout="wide")

# --- 危険な操作（リセット等）のボタンを確実に「赤色」にするCSS ---
st.markdown("""
<style>
div[data-testid="stButton"] button[kind="primary"] {
    background-color: #ff4b4b !important;
    border-color: #ff4b4b !important;
    color: white !important;
}
</style>
""", unsafe_allow_html=True)

# --- 1. アプリのデータ保存用（1週間分に対応） ---
days_of_week = ["月", "火", "水", "木", "金", "土", "日"]
staff_list = ["奥村幸子", "宮崎春秋代", "陰山爽", "徳永久玲美", "北田詩歩", "寺前吏紗", "上田鈴奈", "小鳥美貴"]
time_slots = [f"{h}:{m:02d}" for h in range(10, 18) for m in (0, 30)]

if "settings" not in st.session_state:
    st.session_state.settings = {"start_date": "5/4", "deadline": "4/25"}

# 1週間分のシフトデータを初期化
if "weekly_shifts" not in st.session_state:
    st.session_state.weekly_shifts = {}
    for d in days_of_week:
        data = []
        for s in staff_list:
            row = {"氏名": s, "休み": ""}
            for t in time_slots:
                row[t] = ""
            data.append(row)
        st.session_state.weekly_shifts[d] = pd.DataFrame(data)

# サイドバー
role = st.sidebar.radio("▼ 画面切り替え", ["👨‍💼 管理者画面", "📱 従業員画面"])
st.sidebar.divider()

# ==========================================
# 📱 従業員画面（希望シフト入力）
# ==========================================
if role == "📱 従業員画面":
    st.title("📱 従業員用：希望シフト提出")
    st.info(f"【管理者からのお知らせ】\n対象週: **{st.session_state.settings['start_date']}の週** ／ 提出締切: **{st.session_state.settings['deadline']}**")
    
    staff_name = st.selectbox("あなたの名前を選んでください", [""] + staff_list)
    
    if staff_name:
        st.write(f"**{staff_name}** さんの1週間の希望を入力してください。")
        
        # 曜日ごとにOFF希望を取る
        off_days = st.multiselect("1日お休み（OFF）を希望する曜日を選んでください", days_of_week)
        
        if st.button("希望シフトを提出する"):
            # 選ばれた曜日には「OFF」、それ以外はクリアして即時保存
            for d in days_of_week:
                df = st.session_state.weekly_shifts[d]
                idx = df.index[df['氏名'] == staff_name].tolist()[0]
                df.at[idx, '休み'] = "OFF" if d in off_days else ""
                st.session_state.weekly_shifts[d] = df
            
            st.success("提出が完了しました！管理者の表に即時反映されます。")

# ==========================================
# 👨‍💼 管理者画面
# ==========================================
elif role == "👨‍💼 管理者画面":
    st.title("👨‍💼 管理者用：1週間シフト作成ダッシュボード")
    
    with st.expander("1. シフト設定（開始日・締切）", expanded=False):
        col1, col2 = st.columns(2)
        new_date = col1.text_input("作成する週の開始日（月曜）", st.session_state.settings["start_date"])
        new_deadline = col2.text_input("提出締切日", st.session_state.settings["deadline"])
        if st.button("設定を更新"):
            st.session_state.settings = {"start_date": new_date, "deadline": new_deadline}
            st.success("更新しました。")

    st.divider()
    st.subheader("3 & 4. シフト表の作成と微調整")
    st.write("曜日タブを切り替えて入力してください。マス目をクリックするとプルダウンで選択できます。")

    # 手入力を防ぎ、プルダウンで選択させるためのカラム設定
    col_config = {
        "氏名": st.column_config.TextColumn("氏名", disabled=True),
        "休み": st.column_config.TextColumn("休み", disabled=True), # 従業員の希望をそのまま表示
    }
    for t in time_slots:
        col_config[t] = st.column_config.SelectboxColumn(
            t, options=["", "1", "2", "同", "休"], width="small"
        )

    # 曜日ごとのタブを作成
    tabs = st.tabs(days_of_week)
    
    for i, d in enumerate(days_of_week):
        with tabs[i]:
            # st.data_editorを使ってプルダウン式で編集させる
            edited_df = st.data_editor(
                st.session_state.weekly_shifts[d],
                column_config=col_config,
                use_container_width=True,
                hide_index=True,
                key=f"editor_{d}" # タブごとの誤動作を防ぐID
            )
            # 編集結果を即座に状態へ保存
            st.session_state.weekly_shifts[d] = edited_df

    # ステップ5: 1週間分まとめたExcel出力
    st.divider()
    st.subheader("5. 1週間分のシフトをExcelで出力")
    
    if st.button("📅 1週間分のExcelを出力する"):
        wb = Workbook()
        ws = wb.active
        ws.title = "1週間シフト表"
        
        fill_1 = PatternFill("solid", fgColor="FCE4D6")
        fill_2 = PatternFill("solid", fgColor="FFFF00")
        fill_doujin = PatternFill("solid", fgColor="00B050")
        fill_break = PatternFill("solid", fgColor="FF0000")
        fill_off = PatternFill("solid", fgColor="FF0000")
        border_style = Border(left=Side(style='thin'), right=Side(style='thin'), top=Side(style='thin'), bottom=Side(style='thin'))
        align_center = Alignment(horizontal='center', vertical='center')
        
        current_row = 1 # Excelに書き込む現在の行数
        
        for d in days_of_week:
            df = st.session_state.weekly_shifts[d]
            
            # --- ヘッダー作成 ---
            headers = ["曜日", "氏名", "勤務h", "休憩h", "休み"] + time_slots
            for col_idx, h in enumerate(headers, 1):
                cell = ws.cell(row=current_row, column=col_idx, value=h)
                cell.alignment = align_center
                cell.border = border_style
                if col_idx > 5: ws.column_dimensions[cell.column_letter].width = 4
            
            current_row += 1
            total_counts = {t: 0 for t in time_slots}
            
            # --- 各スタッフのデータ書き込み ---
            for _, row in df.iterrows():
                ws.cell(row=current_row, column=1, value=d).border = border_style
                ws.cell(row=current_row, column=2, value=row["氏名"]).border = border_style
                
                work_slots = sum(1 for t in time_slots if row[t] in ["1", "2", "同"])
                break_slots = sum(1 for t in time_slots if row[t] == "休")
                
                ws.cell(row=current_row, column=3, value=work_slots * 0.5).border = border_style
                ws.cell(row=current_row, column=4, value=break_slots * 0.5).border = border_style
                
                off_cell = ws.cell(row=current_row, column=5, value=row["休み"])
                off_cell.border = border_style
                off_cell.alignment = align_center
                if row["休み"] == "OFF":
                    off_cell.fill = fill_off
                    off_cell.font = Font(color="FFFFFF")
                
                for col_idx, t in enumerate(time_slots, 6):
                    val = row[t]
                    cell = ws.cell(row=current_row, column=col_idx, value=val)
                    cell.border = border_style
                    cell.alignment = align_center
                    
                    if val == "1": cell.fill = fill_1
                    elif val == "2": cell.fill = fill_2
                    elif val == "同": cell.fill = fill_doujin
                    elif val == "休":
                        cell.fill = fill_break
                        cell.font = Font(color="FFFFFF")
                    
                    if val in ["1", "2", "同"]:
                        total_counts[t] += 1
                current_row += 1
                
            # --- 合計ライン作成 ---
            sum_cell = ws.cell(row=current_row, column=2, value="合計ライン")
            sum_cell.alignment = align_center
            sum_cell.fill = fill_2
            
            for col_idx, t in enumerate(time_slots, 6):
                cell = ws.cell(row=current_row, column=col_idx, value=total_counts[t])
                cell.alignment = align_center
                cell.border = border_style
                cell.fill = fill_2
                
            current_row += 2 # 次の曜日の前に1行空ける

        output = io.BytesIO()
        wb.save(output)
        excel_data = output.getvalue()
        
        st.download_button(
            label="📊 1週間分の完成版Excelをダウンロード",
            data=excel_data,
            file_name=f"シフト表_{st.session_state.settings['start_date']}の週.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

st.divider()
if st.button("全データを削除してリセット", type="primary", use_container_width=True):
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    st.rerun()