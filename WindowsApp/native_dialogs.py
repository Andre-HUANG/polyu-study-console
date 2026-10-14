"""Explicit native consent and an unchecked, optional logout-retention checkbox."""
def confirm(window, title, text, ok="同意并开启", checkbox=False):
    import clr
    clr.AddReference("System.Windows.Forms")
    clr.AddReference("System.Drawing")
    from System import Action
    from System.Drawing import Size, Point
    from System.Windows.Forms import (Form, Label, Button, CheckBox, DialogResult, FormBorderStyle, FormStartPosition)
    result = [False, False]
    owner = window.native
    def show():
        dialog = Form()
        dialog.Text = title
        dialog.ClientSize = Size(520, 235)
        dialog.FormBorderStyle = FormBorderStyle.FixedDialog
        dialog.StartPosition = FormStartPosition.CenterParent
        dialog.MaximizeBox = False; dialog.MinimizeBox = False
        label = Label(); label.Text = text; label.Location = Point(18, 18); label.Size = Size(480, 95)
        retain = CheckBox(); retain.Text = "在本机保留已记住的账号密码（仅限个人电脑）"
        retain.Checked = False; retain.Visible = checkbox; retain.Location = Point(18, 122); retain.Size = Size(480, 28)
        yes = Button(); yes.Text = ok; yes.Size = Size(185, 35); yes.Location = Point(190, 178); yes.DialogResult = DialogResult.OK
        no = Button(); no.Text = "取消"; no.Size = Size(100, 35); no.Location = Point(395, 178); no.DialogResult = DialogResult.Cancel
        for control in (label, retain, yes, no): dialog.Controls.Add(control)
        dialog.AcceptButton = yes; dialog.CancelButton = no
        try:
            result[0] = dialog.ShowDialog(owner) == DialogResult.OK
            result[1] = result[0] and checkbox and retain.Checked
        finally: dialog.Dispose()
    if owner.InvokeRequired: owner.Invoke(Action(show))
    else: show()
    return tuple(result)
