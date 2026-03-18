import pandas as pd
import streamlit as st
from pathlib import Path
from typing import Optional

def load_edinetcode_csv(file_path: str) -> Optional[pd.DataFrame]:
    """
    EDINETコードのCSVファイルを読み込む
    
    Args:
        file_path: /data/master/EdinetcodeDlInfo.csv
    Returns:
        pd.DataFrame: 読み込んだデータ
    """
    try:
        # パスをPathオブジェクトに変換
        path = Path(file_path)
        
        # ファイルの存在確認
        if not path.exists():
            print(f"ファイルが見つかりません: {file_path}")
            return None
            
        # CSVファイルを読み込む（ヘッダーなしで読み込む）
        df = pd.read_csv(path, encoding='cp932', header=1)  # 2行目をヘッダーとして読み込む
        
        # 列名を表示
        print("\n=== 読み込んだデータの列名 ===")
        for i, col in enumerate(df.columns):
            print(f"{i}: '{col}'")
        
        # データの最初の行を表示
        print("\n=== 最初の行のデータ ===")
        first_row = df.iloc[0]
        for col in df.columns:
            print(f"'{col}': '{first_row[col]}'")
        
        return df
        
    except Exception as e:
        print(f"エラーが発生しました: {e}")
        return None

def find_company(df: pd.DataFrame, search_term: str) -> None:
    """
    会社名で検索して結果を表示
    
    Args:
        df: EDINETコードのデータフレーム
        search_term: 検索する会社名
    """
    if df is None or len(df) == 0:
        print("検索可能なデータがありません。")
        return
        
    # 検索用に正規化
    def normalize_company_name(name: str) -> str:
        return (name
            .replace('株式会社', '')
            .replace('（株）', '')
            .replace('(株)', '')
            .replace('㈱', '')
            .replace('　', '')
            .replace(' ', '')
            .strip()
            .lower())
    
    normalized_search = normalize_company_name(search_term)
    
    # 正規化した名前で検索
    normalized_names = df['提出者名'].apply(normalize_company_name)
    
    # 完全一致検索
    exact_matches = df[normalized_names == normalized_search]
    
    # 部分一致検索
    partial_matches = df[normalized_names.str.contains(normalized_search, na=False)]
    
    print("\n=== 検索結果 ===")
    print(f"検索語: {search_term}")
    print(f"正規化後: {normalized_search}")
    
    if not exact_matches.empty:
        print("\n完全一致:")
        display_results(exact_matches)
    
    if not partial_matches.empty and len(partial_matches) > len(exact_matches):
        print("\n部分一致:")
        display_results(partial_matches[~partial_matches.index.isin(exact_matches.index)])
        
    if exact_matches.empty and partial_matches.empty:
        print("\n該当する会社が見つかりませんでした。")

def display_results(df: pd.DataFrame) -> None:
    """
    検索結果を表示
    """
    for _, row in df.iterrows():
        print(f"\nEDINETコード: {row['ＥＤＩＮＥＴコード']}")
        print(f"提出者名: {row['提出者名']}")
        print(f"提出者名（英字）: {row['提出者名（英字）']}")
        print(f"上場区分: {row['上場区分']}")
        print(f"証券コード: {row['証券コード']}")

def main():
    # スクリプトの親ディレクトリを基準にパスを設定
    current_dir = Path(__file__).parent.parent  # 一つ上の階層に移動
    csv_path = current_dir / "data" / "master" / "EdinetcodeDlInfo.csv"
    
    print(f"CSVファイルを読み込みます: {csv_path}")
    
    # データの読み込み
    df = load_edinetcode_csv(str(csv_path))
    
    if df is None:
        print("データの読み込みに失敗しました。")
        return
    
    while True:
        # 検索する会社名の入力
        search_term = input("\n検索する会社名を入力してください（終了する場合は'q'）: ")
        
        if search_term.lower() == 'q':
            break
        
        find_company(df, search_term)

if __name__ == "__main__":
    main()