# -*- coding: utf-8 -*-
import logging
import datetime

from openerp import models, fields, api, _
import openerp.addons.decimal_precision as dp

_logger = logging.getLogger(__name__)


class linxo_transaction(models.Model):
    """ Bank Transaction stored on Linxo """
    _name = 'linxo.transaction'

    linxo_id = fields.Integer('Linxo Transaction ID', required=True)
    account_id = fields.Many2one('linxo.account',
                                 'Linxo Account',
                                 ondelete='cascade')
    account_move_line_id = fields.Many2one('account.move.line',
                                           'Account Move Line')
    amount = fields.Float('Amount', digits_compute=dp.get_precision('Account'), required=True)
    date = fields.Date('Date', required=True)
    reconciled = fields.Boolean('Reconciled')
    label = fields.Char('Label', size=255, required=True)
    notes = fields.Char('Notes', size=255)
    journal_id = fields.Many2one(string="Bank Journal",
                                 related="account_id.journal_id",
                                 store=False)

    _defaults = {
        'reconciled': False,
    }

    _sql_constraints = [
        ('linxo_id', 'unique(linxo_id)', 'The account number must be unique'),
    ]

    _rec_name = 'label'
    _order = 'date desc'

    # on change account_move_line_id
    @api.multi
    def apply_reconciliation(self):
        """Mark account move as ok only if amount match
        Also mark invoice as paid, only if amount match
        """

        context = self.env.context

        obj_voucher = self.env['account.voucher']

        do_raise = True
        if 'batch' in context:
            do_raise = False

        for transaction in self:
            if transaction.account_move_line_id:
                account_move_line = transaction.account_move_line_id
                account_move = account_move_line.move_id

                # Find a voucher
                search_args = [('move_id', '=', account_move.id)]
                account_voucher_ids = obj_voucher.search(search_args)
                account_voucher = None
                if not account_voucher_ids:
                    continue
                elif len(account_voucher_ids) > 1:
                    continue
                else:
                    account_voucher = account_voucher_ids

                # Check that balance is 0 and amount match
                if account_move.balance != 0.0:
                    if do_raise:
                        _logger.debug('account_move balance is %s' % account_move.balance)
                        raise Warning(_("Unable to apply reconciliation, the associated move is not balance"))
                    else:
                        continue
                if abs(account_voucher.amount) != abs(transaction.amount):
                    raise_test = True
                    if account_voucher and account_voucher.amount == abs(transaction.amount):
                        raise_test = False

                    if do_raise and raise_test:
                        _logger.debug('account_voucher amount vs transaction amount : %s vs %s' % (account_voucher.amount, transaction.amount))
                        raise Warning(_("Unable to apply reconciliation, the associated voucher amount differs from the transaction"))
                    else:
                        continue

                # So far ok, if draft, make is as OK
                if account_move.state == 'draft':
                    _logger.debug('Marking account_move as validate')
                    account_move.button_validate()

    @api.multi
    def search_reconciliation(self):
        for transaction in self:

            # Already matched ?
            if transaction.account_move_line_id:
                continue

            # From journal we need to extract default debit and credit account
            journal = transaction.journal_id

            search_args = [
                ('journal_id', '=', journal.id),
            ]

            if transaction.amount > 0:
                search_args.append(('debit', '=', transaction.amount))
                search_args.append(('account_id', '=', journal.default_debit_account_id.id))
            else:
                search_args.append(('credit', '=', -transaction.amount))
                search_args.append(('account_id', '=', journal.default_credit_account_id.id))

            date_base = transaction.date
            date_test = [date_base]

            # TODO : limit to 5 ?
            for drift in (1, -1, 2, -2, 3, -3, 4, -4, 5, -5):
                date = datetime.datetime.strptime(date_base, "%Y-%m-%d") + datetime.timedelta(days=int(drift))
                date_test.append(date.strftime("%Y-%m-%d"))

            obj_move_line = self.env['account.move.line']
            move_line_ids = None

            # Perform search starting from today and go back in time
            # Transaction usually appear after date in openerp
            for date in date_test:
                final_search = list(search_args)
                final_search.append(('date', '=', date))
                _logger.debug('searching for date %s' % date)
                _logger.debug(final_search)
                move_line_ids = obj_move_line.search(final_search)
                if move_line_ids:
                    _logger.debug('Found account move line for date %s' % date)
                    # Extra check, do we already have a transaction with this id ?
                    if len(move_line_ids) == 1:
                        test_search = [('account_move_line_id', '=', move_line_ids[0].id)]
                        test_ids = self.search(test_search)
                        if not test_ids:
                            _logger.debug('This account.move.line is unsed, let\'s use it !')
                        else:
                            _logger.debug('This account.move.line is already use, skipping it')
                            continue
                    break

            if not move_line_ids:
                pass
            elif len(move_line_ids) > 1:
                _logger.debug('Find more than one account.move.line')
            else:

                vals = {'account_move_line_id': move_line_ids[0].id, 'reconciled': True}
                self.browse(transaction.id).write(vals)

    @api.multi
    def write(self, vals):
        """Override write to apply reconciliation immediatelly."""
        for record in self:
            super(linxo_transaction, record).write(vals)
            record.apply_reconciliation()

    @api.multi
    def mutual_reconciliation(self):
        """Used when canceling one transaction with another one."""
        self.ensure_one()
        context = self.env.context

        if 'transaction_id' not in context:
            _logger.warning('do_reconciliation problem, context is fucked up')
            _logger.warning(context)
            raise Warning(_("I dont have a transaction associated, this is weird."))

        transaction_id = context['transaction_id']
        for transaction_id in [context['transaction_id'], self.id]:
            _logger.debug('Going to apply reconciliation on transaction id %d' % transaction_id)
            vals = {'reconciled': True}
            self.browse(transaction_id).write(vals)

    @api.multi
    def do_reconciliation(self):
        """Perform reconciliation on all unmark transaction."""
        self.search_reconciliation()

    @api.multi
    def open_wizard(self):
        self.ensure_one()

        context = self.env.context
        if context is None:
            context = {}

        transaction = self
        vals = {'transaction_id': transaction.id, 'date': transaction.date}
        if transaction.amount > 0:
            vals['debit'] = transaction.amount
        else:
            vals['credit'] = -transaction.amount

        # Add transaction id to context
        self.with_context(transaction_id=transaction.id)

        wizard = self.env['linxo.reconcile'].create(vals=vals)
        return {
            'name': 'Reconcile Wizard',
            'view_type': 'form',
            'view_mode': 'form',
            'res_model': 'linxo.reconcile',
            'res_id': wizard.id,
            'type': 'ir.actions.act_window',
            'target': 'new',
            'context': context,
        }
