# Python → EXE 打包腳本

用 `build_exe.bat` 把 Python 程式打包成 Windows 可執行檔（.exe）。

## 使用方式

把 `build_exe.bat` 複製到你的 Python 程式所在的資料夾，然後：

**方法一：自動偵測（資料夾內只有一個 .py 檔時）**
```
.\build_exe.bat
```
會自動找到該資料夾裡唯一的 `.py` 檔並打包。

**方法二：手動指定檔案**
```
.\build_exe.bat 你的程式.py
```

## 執行結果

打包完成後，exe 檔會出現在同資料夾底下的 `dist` 資料夾中。

## 注意事項

- 第一次執行若尚未安裝 `PyInstaller`，腳本會自動幫你安裝
- 若資料夾內有**多個** `.py` 檔，腳本會列出全部檔名，請改用方法二手動指定
- 這個腳本打包的是 **CLI（命令列）模式**，會保留主控台視窗——如果你的程式有用到 `input()` 或需要跟使用者互動，這樣才能正常運作
- 如果你的程式是純 GUI（例如 tkinter、PyQt），完全不需要終端機輸入，可以自行把 `build_exe.bat` 裡的這一行：
  ```
  python -m PyInstaller --onefile "%SCRIPT%"
  ```
  改成加上 `--noconsole`：
  ```
  python -m PyInstaller --onefile --noconsole "%SCRIPT%"
  ```
- 若程式有用到額外的資料檔（圖片、設定檔）或第三方套件有相依問題導致打包失敗，可能需要額外的 `--add-data` 或 `--hidden-import` 參數，歡迎回來問我

## 需求

- 已安裝 Python，且 `python` 指令可在終端機中使用
- Windows 系統（此為 .bat 批次檔）