import { useEffect, useRef } from 'react'

export default function ConfirmDialog({ open, title, description, confirmLabel, cancelLabel = 'Cancel', onConfirm, onCancel }: {
  open: boolean; title: string; description: string; confirmLabel: string; cancelLabel?: string; onConfirm: () => void; onCancel: () => void
}) {
  const dialog = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    if (open && !dialog.current?.open) dialog.current?.showModal()
    if (!open && dialog.current?.open) dialog.current.close()
  }, [open])
  return <dialog ref={dialog} className="confirm-dialog" aria-label={title} onCancel={event => { event.preventDefault(); onCancel() }}>
    <h2>{title}</h2><p>{description}</p><div className="dialog-actions">
      <button className="secondary-button" type="button" autoFocus onClick={onCancel}>{cancelLabel}</button>
      <button className="primary-button" type="button" onClick={onConfirm}>{confirmLabel}</button>
    </div>
  </dialog>
}
