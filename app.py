import streamlit as st
import pandas as pd
from sqlalchemy import create_engine, text
import urllib.parse

st.set_page_config(page_title="デバッグモード", layout="wide")

# --- データベース接続関数（エラー表示強化） ---
@st.cache_resource
def get_engine():
    try:
        # 1. 接続文字列をパースするのではなく、構成要素を直接指定する
        # URIを直接書くのではなく、Secretsに各項目を分けて書くのが理想ですが、
        # 今のURIから安全にパーツを抜き出します。
        
        raw_uri = st.secrets["database"]["uri"]
        
        # URIから情報を抽出（手動パース）
        # postgresql:// [user] : [pass] @ [host] : [port] / [db]
        part1 = raw_uri.split("://")[1]
        user_pass, host_db = part1.split("@")
        user, password = user_pass.split(":", 1)
        host_port, db_name_query = host_db.split("/")
        host, port = host_port.split(":")
        db_name = db_name_query.split("?")[0]

        # 2. SQLAlchemyのURLオブジェクトを作成（これが一番確実）
        url_object = URL.create(
            drivername="postgresql+psycopg2",
            username=user,         # ここに postgres.pism... が確実に入る
            password=password,     # 英字のみなのでそのまま
            host=host,
            port=int(port),
            database=db_name,
            query={"sslmode": "require"},
        )
        
        return create_engine(url_object)
        
    except Exception as e:
        st.error(f"接続設定の分解に失敗しました: {e}")
        return None

# --- メイン処理 ---
st.title("🚀 シフト管理システム：接続テスト中")

engine = get_engine()

if engine:
    try:
        # 実際にDBを叩いてみる
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            st.success("✅ データベース接続に成功しました！")
            
            # テーブル作成を試みる
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
            st.info("テーブルの準備もOKです。")
            
    except Exception as e:
        # 🚨 ここで画面に生のエラーを表示させる
        st.error("⚠️ データベースとの通信に失敗しました。")
        st.exception(e) # これで詳細なスタックトレースが画面に出ます
else:
    st.warning("エンジンが作成されていないため、接続テストをスキップしました。")

st.write("---")
st.write("もし赤いボックスの中に `password authentication failed` と出ていたら、パスワードが間違っています。")
