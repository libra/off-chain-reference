class CommandProcessor:

    def business_context(self):
        ''' Provides the business context associated with this processor.

            Returns:
                The business context of the VASP
                implementing the BusinessContext interface.
        '''
        raise NotImplementedError()

    def check_command(self, vasp, channel, executor, command):
        ''' Called when receiving a new payment command to validate it.

            All checks here are blocking subsequent comments, and therefore they
            must be quick to ensure performance. As a result we only do local
            syntactic checks hat require no lookup into the VASP potentially
            remote stores or accounts.

            Args:
                vasp (OffChainVASP): The current VASP.
                channel (VASPPairChannel):  A VASP channel.
                executor (ProtocolExecutor): The protocol executor.
                command (PaymentCommand): The current payment.
        '''
        raise NotImplementedError()

    def process_command(self, vasp, channel, executor,
                        command, seq, status, error=None):
        """Processes a command to generate more subsequent commands.
            This schedules a task that will be executed later.

            Args:
                vasp (OffChainVASP): The current VASP.
                channel (VASPPairChannel):  A VASP channel.
                executor (ProtocolExecutor): The protocol executor.
                command (PaymentCommand): The current payment command.
                seq (int): The sequence number of the payment command.
                status (bool): Whether the command is a success or failure.
                error (Exception, optional): The exception, if the command is a
                        failure. Defaults to None.

            Returns:
                Future: A task that will be executed later.
        """
        raise NotImplementedError()
